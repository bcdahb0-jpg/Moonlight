"""屏幕感知管理端点（localhost-only）。

- GET  /api/screen/status   —— 采集状态（设置页/状态灯）
- GET  /api/screen/metrics  —— 累计指标（延迟/去重率/token/错误）
- POST /api/screen/analyze  —— 手动分析一帧（调试/用户主动看屏幕）
- POST /api/screen/clear    —— 清除内存上下文（图像+摘要+窗口身份）
- GET  /api/screen/config   —— 当前配置（api_key 打码）
- POST /api/screen/config   —— 写 conf.yaml screen_awareness 块（surgical）

写盘复用 translator_route 的 surgical writer（_find_block_extent / _rewrite_leaf /
_backup_once / _atomic_write），新增块首次创建时插入 system_config 块内。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from . import metrics as metrics_mod
from .analyzer import VisionAnalyzer
from ..llm_config_route import _is_local_request, _forbidden
from .models import ScreenConfig, ScreenFrame, screen_config_from
from .service import get_store
from ..translator_route import (
    _find_block_extent,
    _backup_once,
    _atomic_write,
    _quote_yaml_scalar,
    CONF_PATH,
)

# 配置写入白名单（不含 api_key 的展示层处理见下）。
_CONFIG_WRITE_KEYS = set(ScreenConfig.model_fields.keys()) - {"api_key"}

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_SCREEN_BLOCK_RE = re.compile(r"^(\s*)screen_awareness:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _yaml_render(value: Any) -> str:
    """渲染 YAML 标量：bool/int/float/list 裸写保持类型，str 单引号安全。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(repr(v) for v in value) + "]" if value else "[]"
    return _quote_yaml_scalar(str(value))


def _rewrite_typed_leaf(lines: list, start: int, end: int, key: str, value: Any) -> bool:
    """类型感知改写 'key: value'（保留缩进与行尾注释）。

    找不到该 leaf 时在块末插入新 leaf（首次新增字段用，如 api_key）。
    """
    for j in range(start, end):
        line = lines[j]
        stripped = line.lstrip()
        if stripped.startswith(key + ":"):
            indent_ws = line[: len(line) - len(stripped)]
            comment = ""
            m_comment = re.search(r"(\s+#.*?)\s*$", line.rstrip("\n"))
            if m_comment:
                comment = m_comment.group(1)
            lines[j] = f"{indent_ws}{key}: {_yaml_render(value)}{comment}\n"
            return True
    # 未找到：插入新 leaf（缩进与块内其他 leaf 一致，插在块末）。
    indent = "  "
    # 从 start+1 起找（跳过块头行本身，块头缩进 ≠ leaf 缩进）。
    for j in range(start + 1, min(end, len(lines))):
        stripped = lines[j].lstrip()
        if stripped and not stripped.startswith("#"):
            indent = lines[j][: len(lines[j]) - len(stripped)]
            break
    insert_at = end
    for j in range(end - 1, start - 1, -1):
        if lines[j].strip():
            insert_at = j + 1
            break
    lines[insert_at:insert_at] = [f"{indent}{key}: {_yaml_render(value)}\n"]
    return True


def _find_screen_block(lines: list) -> Optional[tuple[int, int, str]]:
    """返回 (start, end, indent)；不存在返回 None。"""
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = _SCREEN_BLOCK_RE.match(lines[i])
        if m:
            s, _, e = _find_block_extent(lines, _SCREEN_BLOCK_RE, start_from=i)
            return (s, min(e, sys_end), m.group(1))
    return None


def _upsert_screen_block(lines: list, fields: dict[str, Any]) -> bool:
    """把 fields 写进 conf.yaml 的 system_config.screen_awareness 块；无块则创建。"""
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False

    found = _find_screen_block(lines)
    if found is None:
        indent = "  "
        block_lines = [f"{indent}screen_awareness:  # 屏幕理解与陪聊（screen_awareness）\n"]
        for key, value in fields.items():
            block_lines.append(f"{indent}  {key}: {_yaml_render(value)}\n")
        # 插入到 system_config 第一个子块之前（host 等 leaf 之后的安全位置）：
        # 找 system_config 块内第一个缩进子块行。
        insert_at = sys_start + 1
        for i in range(sys_start + 1, min(sys_end, len(lines))):
            if _CHILD_RE.match(lines[i]):
                insert_at = i
                break
        lines[insert_at:insert_at] = block_lines
    else:
        start, end, _indent = found
        for key, value in fields.items():
            _rewrite_typed_leaf(lines, start, end, key, value)
    return True


def apply_screen_config(config: ScreenConfig) -> None:
    """把配置注入全局 store（server.py 启动时与配置热更新时调用）。

    修复（2026-08-11）：必须显式传 ``enabled=config.enabled`` —— store.configure
    只在 enabled 参数非 None 时才更新全局 ``_enabled`` 开关；之前不传导致
    ``is_enabled()`` 恒为 False，帧上传/分析/主动陪聊全部被拒（metrics 恒 0）。
    """
    get_store().configure(config, enabled=config.enabled)
    logger.info(
        f"[screen_awareness] configured (provider={config.provider}, "
        f"enabled={config.enabled})"
    )


def fill_llm_defaults(
    config: ScreenConfig, character_config: Any
) -> ScreenConfig:
    """继承对话 LLM 配置作为视觉 provider 缺省（inherit 语义）。

    未显式配置 base_url/model/api_key 时，用 character_config 的
    openai_compatible_llm 填充——用户把对话模型指向支持视觉的端点
    （gpt-4o / qwen-vl / ollama llava 等）即可直接工作；DeepSeek 等纯
    文本模型不支持视觉时 analyze 返回 None，fail-soft 不阻塞聊天。
    """
    if config.base_url:
        return config
    try:
        llm = character_config.agent_config.llm_configs.openai_compatible_llm
        base = str(getattr(llm, "base_url", "") or "")
        model = str(getattr(llm, "model", "") or "")
        key = str(getattr(llm, "llm_api_key", "") or "")
        if base and model:
            config.base_url = base
            config.model = model
            config.api_key = key
    except Exception:
        pass
    return config


def init_screen_route() -> APIRouter:
    router = APIRouter()
    store = get_store()

    # 注：不再在这里 apply 默认配置——server.py 启动时会用 conf.yaml 的
    # screen_awareness 块（含硅基流动视觉 provider）注入 store；此处若再次
    # apply_screen_config(ScreenConfig()) 会把真实配置覆盖成默认（provider 丢失）。
    # store 构造函数默认即 ScreenConfig()，无需兜底。

    @router.get("/api/screen/status")
    async def screen_status(request: Request, uid: str = ""):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(
            store.status(uid or "default").model_dump(mode="json")
        )

    @router.get("/api/screen/metrics")
    async def screen_metrics(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        snap = metrics_mod.get_metrics().snapshot()
        # Phase 5：成本估算（USD）——按 conf 单价 × 实际输入/输出 token。
        cfg = store.config()
        pi = float(cfg.price_input_per_mtok or 0)
        po = float(cfg.price_output_per_mtok or 0)
        snap["estimated_cost_usd"] = round(
            snap["vision_input_tokens"] / 1_000_000 * pi
            + snap["vision_output_tokens"] / 1_000_000 * po,
            6,
        )
        snap["price_input_per_mtok"] = pi
        snap["price_output_per_mtok"] = po
        return JSONResponse(snap)

    @router.post("/api/screen/feedback")
    async def screen_feedback(request: Request):
        """识别反馈：useful（有用）/ disruptive（打扰）/ misrecognition（识别错误）。
        body: {"uid": "...", "kind": "useful|disruptive|misrecognition", "comment": "..."}
        仅累计本地指标，用于 ProactivePolicy 阈值调优；不落盘原始截图。
        """
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        kind = str((body or {}).get("kind") or "").strip()
        if kind not in ("useful", "disruptive", "misrecognition"):
            return JSONResponse(status_code=400, content={"ok": False, "error": "kind must be useful|disruptive|misrecognition"})
        m = metrics_mod.get_metrics()
        m.inc(f"feedback_{kind}")
        logger.info(f"[screen_awareness] feedback={kind} (uid={body.get('uid') or 'default'})")
        return JSONResponse({"ok": True})

    @router.post("/api/screen/analyze")
    async def screen_analyze(request: Request):
        """body: ScreenFrame JSON（含 image data URL）。用于调试与用户主动看屏幕。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
            frame = ScreenFrame.model_validate(body)
        except Exception as e:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": f"invalid frame: {e}"}
            )
        cfg = store.config()
        status = await store.ingest(
            "default", frame, api_key=cfg.api_key
        )
        return JSONResponse({"ok": True, "status": status.model_dump(mode="json")})

    @router.post("/api/screen/clear")
    async def screen_clear(request: Request, uid: str = ""):
        if not _is_local_request(request):
            return _forbidden()
        if uid == "*":
            store.clear_all()
        else:
            store.clear(uid or "default")
        return JSONResponse({"ok": True})

    @router.get("/api/screen/config")
    async def screen_get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        cfg = store.config()
        data = cfg.model_dump(mode="json")
        key = str(cfg.api_key or "")
        data["api_key"] = f"{key[:4]}****" if key else ""
        data["provider_label"] = VisionAnalyzer(cfg).provider_label
        return JSONResponse(data)

    @router.post("/api/screen/config")
    async def screen_set_config(request: Request):
        """body: 部分字段（白名单）。api_key 单独处理（不回显）。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid body"})

        # 读取当前配置，合并用户提交的白名单字段。
        current = store.config()
        merged = current.model_dump(mode="json")
        for key, value in body.items():
            if key not in _CONFIG_WRITE_KEYS and key != "api_key":
                continue
            if key == "api_key":
                # 空串 = 保持不变；'****' 开头 = 前端回显占位，忽略。
                v = str(value or "")
                if v and not v.startswith("****"):
                    merged["api_key"] = v
                continue
            if value is not None:
                merged[key] = value

        new_config = ScreenConfig.model_validate(merged)
        fields = {k: v for k, v in new_config.model_dump(mode="json").items()}
        fields.pop("api_key", None)

        try:
            with open(CONF_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not _upsert_screen_block(lines, fields):
                return JSONResponse(status_code=500, content={"ok": False, "error": "system_config block not found"})
            _backup_once()
            _atomic_write(lines)
        except Exception as e:
            logger.error(f"[screen_awareness] config write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "config write failed"})

        apply_screen_config(new_config)
        return JSONResponse({"ok": True, "restart_required": False})

    return router
