"""表情 / 口型动作路由（P1 表情与动作域）。

- POST /api/expression/motion-plan {text, duration_sec}
    LLM 生成逐秒 Live2D 参数帧序列（供 TTS 播放期间逐帧应用）。
    参数白名单 + min/max clamp；MouthOpen 开合参数被过滤（口型交由 TTS
    音量驱动覆盖），保留 MouthForm 等嘴型表达。LLM 失败 fail-soft 返回
    单帧空参数。
- POST /api/expression/generate {text}
    复用 emotion_classifier（规则快路径 + LLM 慢路径）返回
    {emotion, intensity, duration_ms}，并写入全局情绪跟踪器（前端既有
    emotion → setExpression 链路自动消化）。手动触发的「AI 表情」测试入口。
- GET  /api/expression/config
- POST /api/expression/config {enabled, sensitivity, amplitude, easing, model}
    读写 conf.yaml `system_config.expression` 块（surgical upsert，参照
    screen_route 的块级写入模式）。

参考：reference/SoulLink_Live2D/src/generators/expression.py
（EXPRESSION_PARAM_MAPPING 通用参数思路 + _clamp_parameters + _filter_mouth_params）
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden
from .translator_route import _find_block_extent, _backup_once, _atomic_write, _quote_yaml_scalar, CONF_PATH
from .emotion.emotion_classifier import classify, _parse_llm_json, EMOTIONS
from .emotion import get_emotion_tracker

# --------------------------------------------------------------------------- #
# 参数白名单与 clamp 表（移植 SoulLink _clamp_parameters 思路）
# --------------------------------------------------------------------------- #

# (param_id, min, max) —— 帧序列只允许这些参数，范围与 Live2D 模型通用约定对齐。
PARAM_RANGES: dict[str, tuple[float, float]] = {
    "ParamEyeLOpen": (-1.0, 1.0),
    "ParamEyeROpen": (-1.0, 1.0),
    "ParamEyeBallX": (-1.0, 1.0),
    "ParamEyeBallY": (-1.0, 1.0),
    "ParamBrowLY": (-1.0, 1.0),
    "ParamBrowRY": (-1.0, 1.0),
    "ParamMouthOpenY": (0.0, 1.0),
    "ParamMouthForm": (-1.0, 1.0),
    "ParamCheek": (0.0, 1.0),
    "ParamAngleX": (-30.0, 30.0),
    "ParamAngleY": (-30.0, 30.0),
    "ParamAngleZ": (-30.0, 30.0),
    "ParamBodyAngleX": (-12.0, 12.0),
    "ParamBodyAngleY": (-12.0, 12.0),
    "ParamBodyAngleZ": (-12.0, 12.0),
}

# 口型开合参数：TTS 音量驱动会覆盖，帧序列里剔除（保留 MouthForm 等嘴型）。
_MOUTH_OPEN_KEYS = {"parammouthopen", "parammouthopeny", "mouthopen", "openmouth", "open_mouth"}

# 默认 config（conf.yaml system_config.expression 缺失时）。
DEFAULT_CONFIG = {
    "enabled": True,
    "sensitivity": 60,
    "amplitude": 70,
    "easing": True,
    "model": "",
}
_CONFIG_INT_KEYS = ("sensitivity", "amplitude")
_CONFIG_BOOL_KEYS = ("enabled", "easing")


# --------------------------------------------------------------------------- #
# 参数处理
# --------------------------------------------------------------------------- #

def _clamp_parameters(parameters: dict, amplitude: float = 1.0) -> dict:
    """白名单 + clamp + 幅度缩放。amplitude 0..100 → 0..1 缩放非角度参数。"""
    factor = max(0.0, min(1.0, amplitude / 100.0))
    out: dict[str, float] = {}
    for key, raw in (parameters or {}).items():
        norm = key.lower()
        if norm in _MOUTH_OPEN_KEYS:
            continue  # 口型开合过滤
        if key not in PARAM_RANGES:
            continue  # 非白名单参数忽略
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        lo, hi = PARAM_RANGES[key]
        value = max(lo, min(hi, value))
        if not key.startswith("ParamAngle") and not key.startswith("ParamBodyAngle"):
            value = lo + (value - lo) * factor  # 表情类参数按幅度缩放
        out[key] = round(value, 3)
    return out


# --------------------------------------------------------------------------- #
# LLM 生成
# --------------------------------------------------------------------------- #

async def _llm_motion_plan(text: str, duration_sec: float) -> Optional[list]:
    """LLM 生成逐秒帧序列；失败返回 None（调用方 fail-soft）。"""
    try:
        from ..task_platform import conf_bridge, graph  # noqa: PLC0415
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        cfg = conf_bridge.task_config()
        model = graph.build_model(cfg)
    except Exception as e:
        logger.debug(f"[expression] motion-plan LLM build failed: {type(e).__name__}")
        return None

    frame_count = max(1, min(120, int(duration_sec) + 1))
    param_docs = "\n".join(f"  {k}: {lo}~{hi}" for k, (lo, hi) in PARAM_RANGES.items() if k != "ParamMouthOpenY")
    prompt = (
        "为下面这段角色台词生成 Live2D 逐秒动作表情帧。只输出一个 JSON 对象，不要任何其他文字。\n"
        "格式：{\"frames\": [{\"secondIndex\": 0, \"action\": \"2-8字动作描述\", "
        "\"parameters\": {\"ParamAngleX\": -5, \"ParamMouthForm\": 0.3, ...}}]}\n"
        f"共 {frame_count} 帧（secondIndex 从 0 到 {frame_count - 1}，每帧 1 秒）。\n"
        "参数名只能使用以下白名单（范围如下）：\n"
        f"{param_docs}\n"
        "要求：\n"
        "1. 帧间连贯自然，表达台词语气（惊讶瞪眼、难过垂眉、开心扬嘴等），不要每帧大动作。\n"
        "2. 不要输出 ParamMouthOpenY（口型开合交给 TTS 自动同步）。\n"
        "3. 参数缺省表示保持上一帧（首帧缺省表示自然中性）。\n"
        f"台词：{text[:300]}"
    )
    try:
        import asyncio

        resp = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=prompt), HumanMessage(content="")]),
            timeout=6.0,
        )
        raw = str(getattr(resp, "content", "") or "").strip()
        parsed = _parse_llm_json(raw)
        frames = (parsed or {}).get("frames")
        if not isinstance(frames, list) or not frames:
            return None
        # 归一化 secondIndex + 过滤非法参数（clamp 在路由层统一做，避免二次 LLM 调用）
        normalized = []
        for i, frame in enumerate(frames[:frame_count]):
            if not isinstance(frame, dict):
                continue
            normalized.append(
                {
                    "secondIndex": int(frame.get("secondIndex", i)),
                    "action": str(frame.get("action") or "")[:32],
                    "parameters": dict(frame.get("parameters") or {}),
                }
            )
        return normalized
    except Exception as e:
        logger.debug(f"[expression] motion-plan LLM failed: {type(e).__name__}: {e}")
        return None


# --------------------------------------------------------------------------- #
# conf.yaml 块级写入（参照 screen_route 的 _upsert_screen_block）
# --------------------------------------------------------------------------- #

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_EXPR_BLOCK_RE = re.compile(r"^(\s*)expression:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _yaml_render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml_scalar(str(value))


def _upsert_expression_block(fields: dict) -> bool:
    """把 fields 写进 conf.yaml 的 system_config.expression 块；无块则创建。"""
    with open(CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False

    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = _EXPR_BLOCK_RE.match(lines[i])
        if m:
            s, _, e = _find_block_extent(lines, _EXPR_BLOCK_RE, start_from=i)
            found = (s, min(e, sys_end), m.group(1))
            break

    if found is None:
        indent = "  "
        block_lines = [f"{indent}expression:  # 表情与口型动作（expression_route，P1）\n"]
        for key, value in fields.items():
            block_lines.append(f"{indent}  {key}: {_yaml_render(value)}\n")
        insert_at = sys_start + 1
        for i in range(sys_start + 1, min(sys_end, len(lines))):
            if _CHILD_RE.match(lines[i]):
                insert_at = i
                break
        lines[insert_at:insert_at] = block_lines
    else:
        start, end, _indent = found
        for key, value in fields.items():
            for j in range(start, end):
                stripped = lines[j].lstrip()
                if stripped.startswith(key + ":"):
                    indent_ws = lines[j][: len(lines[j]) - len(stripped)]
                    comment = ""
                    m_comment = re.search(r"(\s+#.*?)\s*$", lines[j].rstrip("\n"))
                    if m_comment:
                        comment = m_comment.group(1)
                    lines[j] = f"{indent_ws}{key}: {_yaml_render(value)}{comment}\n"
                    break
            else:
                # 新字段插入块末
                indent = "  "
                for j in range(start + 1, min(end, len(lines))):
                    s = lines[j].lstrip()
                    if s and not s.startswith("#"):
                        indent = lines[j][: len(lines[j]) - len(s)]
                        break
                lines[end:end] = [f"{indent}{key}: {_yaml_render(value)}\n"]

    _backup_once()
    _atomic_write(lines)
    return True


def _expression_config_from_conf() -> dict:
    """读 conf.yaml system_config.expression（缺失返回默认）。"""
    try:
        from .config_manager.utils import read_yaml  # noqa: PLC0415

        data = read_yaml(CONF_PATH) or {}
        block = ((data.get("system_config", {}) or {}).get("expression", {}) or {})
        out = dict(DEFAULT_CONFIG)
        for key in DEFAULT_CONFIG:
            if key in block and block[key] is not None:
                out[key] = block[key]
        return out
    except Exception:
        return dict(DEFAULT_CONFIG)


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #

def init_expression_route() -> APIRouter:
    router = APIRouter()

    @router.post("/api/expression/motion-plan")
    async def motion_plan(request: Request):
        """body: {"text": "...", "duration_sec": 8.0} → 逐秒参数帧序列。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid body"})
        text = str(body.get("text") or "").strip()[:300]
        if not text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "text required"})
        try:
            duration_sec = max(1.0, min(120.0, float(body.get("duration_sec") or 5.0)))
        except (TypeError, ValueError):
            duration_sec = 5.0

        cfg = _expression_config_from_conf()
        amplitude = float(cfg.get("amplitude") or 70)

        frames = await _llm_motion_plan(text, duration_sec)
        frame_count = max(1, min(120, int(duration_sec) + 1))
        if not frames:
            frames = [{"secondIndex": i, "action": "自然动作", "parameters": {}} for i in range(frame_count)]

        cleaned = []
        for frame in frames:
            cleaned.append(
                {
                    "secondIndex": int(frame.get("secondIndex", 0)),
                    "action": frame.get("action", ""),
                    "parameters": _clamp_parameters(frame.get("parameters") or {}, amplitude),
                }
            )
        cleaned.sort(key=lambda f: f["secondIndex"])
        return JSONResponse(
            {
                "ok": True,
                "duration_sec": duration_sec,
                "frames": cleaned,
                "source": "llm" if len(frames) == frame_count and any(f.get("parameters") for f in frames) else "fallback",
            }
        )

    @router.post("/api/expression/generate")
    async def generate(request: Request):
        """body: {"text": "..."} → 情绪分类 + 写情绪跟踪器（前端表情链路消费）。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        text = str((body or {}).get("text") or "").strip()[:1000]
        if not text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "text required"})
        result = await classify(text)
        get_emotion_tracker().update(result.emotion, result.intensity, source="expression")
        return JSONResponse({"ok": True, **result.to_dict()})

    @router.get("/api/expression/config")
    async def get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(_expression_config_from_conf())

    @router.post("/api/expression/config")
    async def set_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid body"})

        fields = {}
        for key, value in body.items():
            if key in _CONFIG_INT_KEYS:
                try:
                    fields[key] = max(0, min(100, int(value)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": f"{key} must be int"})
            elif key in _CONFIG_BOOL_KEYS:
                fields[key] = bool(value)
            elif key == "model":
                fields[key] = str(value or "").strip()[:200]
        if not fields:
            return JSONResponse(status_code=400, content={"ok": False, "error": "nothing to update"})

        try:
            _upsert_expression_block(fields)
        except Exception as e:
            logger.error(f"[expression] config write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "config write failed"})
        return JSONResponse({"ok": True, **_expression_config_from_conf(), "restart_required": False})

    return router
