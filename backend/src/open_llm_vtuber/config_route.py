"""统一配置 API（Phase 1：配置单一事实源收口）。

Endpoints（localhost-only，复用 llm_config_route 的本地鉴权）:

- GET /api/config          -> 完整配置（pydantic 校验 + API key 脱敏）
- PUT /api/config          -> JSON Merge Patch 局部更新（深度合并 -> 校验 -> 原子写盘 -> 热重载）
- GET /api/config/schema   -> 配置 JSON Schema（Phase 0 已实现，在 routes.py）

设计要点：
- conf.yaml 是唯一事实源；写盘走 ruamel round-trip（保留全部注释），原子写（temp + os.replace）
- PUT 是 JSON Merge Patch（RFC 7386 语义）：只更新 patch 中出现的键，嵌套对象递归合并；
  `null` 视为「把该字段置空」而不是删除（conf.yaml 中 null 字段有业务含义，避免误删）
- 校验先行：patch 合并后先过 pydantic Config 校验，失败返回 400 且不写盘
- 热重载：写盘成功后对 default_context_cache 执行 load_from_config（新连接立即生效），
  并向所有在线前端广播 config-updated；热重载自身失败不阻塞请求（磁盘已更新，提示重启）
- API key 永不打日志、读回一律脱敏
"""
import asyncio
import os
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .service_context import ServiceContext
from .contracts import send_message
from .llm_config_route import (
    _forbidden,
    _is_local_request,
    _load_conf,
    _make_yaml,
    _mask_key,
)
from .config_manager.utils import read_yaml, validate_config

CONF_PATH = "conf.yaml"

# key 名含这些片段的值一律脱敏（llm_config_route._mask_key）。
_SENSITIVE_KEY_FRAGMENTS = ("api_key", "apikey", "secret", "token")


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def _deep_merge(base: Any, patch: Any) -> Any:
    """JSON Merge Patch 深度合并。非 dict 直接替换；dict 递归合并。"""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _mask_config(data: Any) -> Any:
    """递归脱敏：key 名含敏感片段的字符串值 -> 掩码；非字符串原样保留。"""
    if isinstance(data, dict):
        return {
            k: _mask_key(v) if _is_sensitive(k) and isinstance(v, str) else _mask_config(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_config(item) for item in data]
    return data


def _is_sensitive(key: str) -> bool:
    lowered = str(key).lower()
    return any(frag in lowered for frag in _SENSITIVE_KEY_FRAGMENTS)


def _dump_yaml_atomic(data: Any) -> None:
    """ruamel round-trip 原子写盘：保留注释 + temp 文件 + os.replace。"""
    yaml = _make_yaml()
    conf_dir = os.path.dirname(os.path.abspath(CONF_PATH)) or "."
    tmp_path = os.path.join(conf_dir, ".conf.yaml.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    os.replace(tmp_path, CONF_PATH)


async def _hot_reload(context: ServiceContext) -> str | None:
    """写盘成功后重载默认上下文。返回 None=成功，否则返回用户可读警告。"""
    try:
        config = validate_config(read_yaml(CONF_PATH))
        await context.load_from_config(config)
        return None
    except Exception as e:
        logger.exception(f"config hot-reload failed: {type(e).__name__}: {e}")
        return "配置已保存，但热重载失败（请重启后生效）。详见后端日志。"


async def _broadcast_config_updated() -> None:
    """向所有在线前端广播 config-updated（静默失败）。"""
    try:
        from .websocket_handler import get_ws_handler

        handler = get_ws_handler()
        if handler is None:
            return
        for uid, ws in list(handler.client_connections.items()):
            try:
                await send_message(ws.send_text, {"type": "config-updated"})
            except Exception:
                logger.debug(f"config-updated broadcast to {uid} failed")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 路由工厂
# --------------------------------------------------------------------------- #

def init_config_route(default_context_cache: ServiceContext) -> APIRouter:
    """统一配置 API（localhost-only）。"""
    router = APIRouter()

    @router.get("/api/config")
    async def get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            validated = validate_config(read_yaml(CONF_PATH))
            data = validated.model_dump(by_alias=True, exclude_none=False)
            return JSONResponse(_mask_config(data))
        except Exception as e:
            logger.error(f"config read failed: {type(e).__name__}")
            return JSONResponse(
                status_code=500, content={"error": "could not read config"}
            )

    @router.put("/api/config")
    async def put_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Invalid JSON body."}
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Patch must be a JSON object."},
            )
        if not body:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Empty patch."},
            )

        # 1. 合并（在 ruamel CommentedMap 上就地合并，保留注释）。
        try:
            current = _load_conf()
        except Exception as e:
            logger.error(f"config merge load failed: {type(e).__name__}")
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "could not read config"}
            )

        merged = _deep_merge(current, body)

        # 2. 校验先行：非法 patch 一律不写盘。
        try:
            validate_config(dict(merged))
        except Exception as e:
            logger.warning(f"config patch rejected: {type(e).__name__}")
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"配置校验失败：{type(e).__name__}，未写入。"},
            )

        # 3. 原子写盘。
        try:
            await asyncio.to_thread(_dump_yaml_atomic, merged)
        except Exception as e:
            logger.error(f"config write failed: {type(e).__name__}")
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "Could not write config file."}
            )

        # 4. 热重载 + 广播（失败不阻塞响应）。
        warning = await _hot_reload(default_context_cache)
        await _broadcast_config_updated()

        return JSONResponse(
            {
                "ok": True,
                "warning": warning,
                "config": _mask_config(validate_config(read_yaml(CONF_PATH)).model_dump(
                    by_alias=True, exclude_none=False
                )),
            }
        )

    return router
