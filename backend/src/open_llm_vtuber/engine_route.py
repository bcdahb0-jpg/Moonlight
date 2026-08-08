"""
Engine management endpoints — ASR/TTS 引擎配置管理（设置面板 + 角色卡）。

- GET   /api/engines                 -> 全部引擎 + 配置状态 + 字段 schema（scope=tts|asr|all）
- GET   /api/engines/configured      -> 已配置可用的引擎（角色卡下拉数据源）
- POST  /api/engines/{engine}        -> 写某引擎子块的字段（通用，按 schema 白名单）
- GET   /api/engines/voicevox/status -> VOICEVOX 本地引擎状态
- POST  /api/engines/voicevox/download -> 下载 VOICEVOX 引擎（后台，轮询 status 看进度）
- POST  /api/engines/voicevox/start  -> 启动 VOICEVOX 引擎
- POST  /api/engines/voicevox/stop   -> 停止 VOICEVOX 引擎

设计：
- 引擎元数据与配置状态判定集中在 engine_catalog.py（角色卡/设置面板共用）。
- 写入复用 translator_route 的 surgical writer（_find_block_extent / _rewrite_leaf /
  _rewrite_int_leaf / _backup_once / _atomic_write），scoped 到引擎子块，绝不误伤兄弟块。
- 只允许写 schema 里声明的字段（白名单）；密码类字段永远不回显原始值。
"""

import re
import asyncio
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden
from .translator_route import (
    _find_block_extent,
    _rewrite_leaf,
    _backup_once,
    _atomic_write,
    CONF_PATH,
)
from .memory_route import _rewrite_int_leaf
from . import engine_catalog
from .voicevox_manager import VoiceVoxManager


# --------------------------------------------------------------------------- #
# 写入：通用引擎子块字段更新
# --------------------------------------------------------------------------- #
def _block_extent(lines: list, scope: str) -> tuple[int, int]:
    key = "asr_config" if scope == "asr" else "tts_config"
    rex = re.compile(r"^(\s*)" + re.escape(key) + r":\s*(#.*)?$")
    start, _, end = _find_block_extent(lines, rex)
    if start is None:
        raise KeyError(f"{key}: line not found in conf.yaml")
    return start + 1, end


def _engine_extent(
    lines: list, parent_start: int, parent_end: int, engine: str
) -> tuple[int, int]:
    rex = re.compile(r"^(\s*)" + re.escape(engine) + r":\s*(#.*)?$")
    s, _, e = _find_block_extent(lines, rex, start_from=parent_start)
    if s is None or s >= parent_end:
        raise KeyError(f"{engine}: sub-block not found in {parent_end and 'tts_config' or 'asr_config'}")
    return s + 1, min(e, parent_end)


def _write_engine_fields(scope: str, engine: str, fields: dict[str, Any]) -> bool:
    """把 fields 写进 conf.yaml 的 <scope>.<engine> 子块（字段白名单 + 类型感知）。"""
    catalog = engine_catalog.TTS_CATALOG if scope == "tts" else engine_catalog.ASR_CATALOG
    meta = catalog.get(engine)
    if meta is None:
        raise KeyError(f"unknown engine: {engine}")
    allowed = {f["key"]: f for f in meta["fields"]}

    with open(CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    parent_start, parent_end = _block_extent(lines, scope)
    es, ee = _engine_extent(lines, parent_start, parent_end, engine)

    for key, value in fields.items():
        field = allowed.get(key)
        if field is None:
            continue  # 白名单外字段忽略（不报错，防盲写）
        raw = "" if value is None else str(value).strip()
        if not raw:
            continue
        if field["type"] == "number":
            try:
                iv = int(float(raw))
            except (TypeError, ValueError):
                raise ValueError(f"{key} 需要是数字")
            if not _rewrite_int_leaf(lines, es, ee, key, iv):
                raise KeyError(f"{key} leaf not found in {engine}")
        else:
            if not _rewrite_leaf(lines, es, ee, key, raw):
                raise KeyError(f"{key} leaf not found in {engine}")

    _backup_once()
    _atomic_write(lines)
    return True


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #
def init_engine_route() -> APIRouter:
    router = APIRouter()
    mgr = VoiceVoxManager()

    @router.get("/api/engines")
    async def get_engines(request: Request, scope: str = "all"):
        """scope: tts | asr | all。返回引擎列表（含配置状态 + 字段 schema）。"""
        if not _is_local_request(request):
            return _forbidden()
        out = {}
        if scope in ("tts", "all"):
            out["tts"] = engine_catalog.get_engines("tts")
        if scope in ("asr", "all"):
            out["asr"] = engine_catalog.get_engines("asr")
        return JSONResponse(out)

    @router.get("/api/engines/configured")
    async def get_configured(request: Request, scope: str = "tts"):
        """仅已配置可用的引擎（角色卡下拉用）。scope=tts|asr。"""
        if not _is_local_request(request):
            return _forbidden()
        key = "tts" if scope == "tts" else "asr"
        return JSONResponse({"scope": key, "engines": engine_catalog.get_configured_engines(key)})

    @router.post("/api/engines/{engine}")
    async def set_engine_config(request: Request, engine: str):
        """body: {"scope": "tts"|"asr", "fields": {"api_key": "...", ...}}"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "Invalid JSON body."})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "Invalid JSON body."})
        scope = str(body.get("scope") or "tts").strip()
        if scope not in ("tts", "asr"):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "scope must be tts or asr."})
        fields = body.get("fields")
        if not isinstance(fields, dict) or not fields:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "fields is required."})

        try:
            await asyncio.to_thread(_write_engine_fields, scope, engine, fields)
        except KeyError as e:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": f"{e}"})
        except ValueError as e:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": f"{e}"})
        except Exception as e:
            logger.error(f"engine write failed {engine}: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500,
                                content={"ok": False, "error": "Could not write config file."})

        # 回读掩码状态（绝不回显明文密钥）
        info = engine_catalog.get_engine(scope, engine)
        logger.info(f"engine config saved ({scope}/{engine})")
        return JSONResponse({"ok": True, "engine": info, "restart_required": True})

    # ------------------------------------------------------------------ //
    # VOICEVOX 本地引擎管理
    # ------------------------------------------------------------------ //
    @router.get("/api/engines/voicevox/status")
    async def voicevox_status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse({"ok": True, "voicevox": mgr.status()})

    @router.post("/api/engines/voicevox/download")
    async def voicevox_download(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
            use_mirror = bool((body or {}).get("use_mirror", False))
        except Exception:
            use_mirror = False
        started = mgr.download_async(use_mirror=use_mirror)
        if not started:
            return JSONResponse({"ok": False, "error": "下载任务已在运行中"})
        return JSONResponse({"ok": True, "msg": "下载已开始（几百 MB，请稍候）"})

    @router.post("/api/engines/voicevox/start")
    async def voicevox_start(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        result = mgr.start()
        return JSONResponse({"ok": result["ok"], **result})

    @router.post("/api/engines/voicevox/stop")
    async def voicevox_stop(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        result = mgr.stop()
        return JSONResponse({"ok": result["ok"], **result})

    return router
