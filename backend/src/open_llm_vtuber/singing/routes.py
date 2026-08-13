"""唱歌与音乐 REST 端点（P2 唱歌 MVP）。

- POST /api/singing/request {text}          → 提取「唱歌+歌名」→ 入队学歌
- GET  /api/singing/status                  → 状态机 / 队列 / 可播列表 / 配置
- POST /api/singing/next                    → 出队下一首可播歌曲（前端播放，播完再调）
- POST /api/singing/stop_learning           → 停止当前学歌
- POST /api/singing/clear                   → 清空点歌队列与可播列表
- GET  /api/singing/config / POST ...       → conf system_config.singing（surgical upsert）
- GET  /api/singing/engine/status           → so-vits 翻唱引擎探测（P2 基础）
- POST /api/singing/convert                 → so-vits 整曲转换（P2 基础，需 svc_url 配置）

参考：reference/AI-YinMei/func/sing/sing_core.py（队列+学歌轮询+切歌）、
reference/so-vits-svc/flask_api_full_song.py（整曲转换 HTTP 契约）
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from ..llm_config_route import _is_local_request, _forbidden
from ..translator_route import _find_block_extent, _backup_once, _atomic_write, _quote_yaml_scalar, CONF_PATH
from .sing_core import DEFAULT_CONFIG, SingCore, extract_sing_request, get_sing_core

# --------------------------------------------------------------------------- #
# conf.yaml system_config.singing 块（surgical upsert，参照 expression_route）
# --------------------------------------------------------------------------- #

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_SINGING_BLOCK_RE = re.compile(r"^(\s*)singing:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _yaml_render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml_scalar(str(value))


def _upsert_singing_block(fields: dict) -> bool:
    with open(CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False

    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = _SINGING_BLOCK_RE.match(lines[i])
        if m:
            s, _, e = _find_block_extent(lines, _SINGING_BLOCK_RE, start_from=i)
            found = (s, min(e, sys_end), m.group(1))
            break

    if found is None:
        indent = "  "
        block_lines = [f"{indent}singing:  # 唱歌与音乐（singing_route，P2）\n"]
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


def _singing_config_from_conf() -> dict:
    try:
        from ..config_manager.utils import read_yaml  # noqa: PLC0415

        data = read_yaml(CONF_PATH) or {}
        block = ((data.get("system_config", {}) or {}).get("singing", {}) or {})
        out = dict(DEFAULT_CONFIG)
        for key in DEFAULT_CONFIG:
            if key in block and block[key] is not None:
                out[key] = block[key]
        return out
    except Exception:
        return dict(DEFAULT_CONFIG)


# --------------------------------------------------------------------------- #
# so-vits 翻唱引擎（P2 基础：探测 + 转换调用）
# --------------------------------------------------------------------------- #

async def _probe_svc(svc_url: str) -> dict:
    """探测 so-vits flask_api 是否在线（fail-soft）。"""
    import aiohttp  # noqa: PLC0415

    out = {"configured": bool(svc_url), "online": False, "error": None}
    if not svc_url:
        return out
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(svc_url.rstrip("/") + "/", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                out["online"] = resp.status < 500
    except Exception as e:
        out["error"] = f"{type(e).__name__}"
    return out


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #

def init_singing_route() -> APIRouter:
    router = APIRouter()
    core_holder: dict[str, Optional[SingCore]] = {"core": None}

    async def _core() -> SingCore:
        if core_holder["core"] is None:
            core_holder["core"] = await get_sing_core()
        return core_holder["core"]

    @router.post("/api/singing/request")
    async def sing_request(request: Request):
        """body: {"text": "唱歌+打上花火"} 或 {"songname": "打上花火"}。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        text = str((body or {}).get("text") or "").strip()
        if not text:
            text = str((body or {}).get("songname") or "").strip()
            if text:
                text = "唱歌+" + text
        if not text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "text or songname required"})
        core = await _core()
        result = await core.request(text)
        return JSONResponse(result if result["ok"] else {**result, "status_code": 200})

    @router.get("/api/singing/status")
    async def sing_status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        core = await _core()
        return JSONResponse(core.status())

    @router.post("/api/singing/next")
    async def sing_next(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        core = await _core()
        return JSONResponse(await core.next_song())

    @router.post("/api/singing/stop_learning")
    async def sing_stop_learning(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        core = await _core()
        return JSONResponse(await core.stop_learning())

    @router.post("/api/singing/clear")
    async def sing_clear(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        core = await _core()
        return JSONResponse(await core.clear())

    @router.get("/api/singing/config")
    async def get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(_singing_config_from_conf())

    @router.post("/api/singing/config")
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
            if key == "acm_url":
                fields[key] = str(value or "").strip()[:200]
            elif key == "create_timeout":
                try:
                    fields[key] = max(30, min(3600, int(value)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": "create_timeout must be int"})
            elif key == "song_not_convert":
                fields[key] = str(value or "").strip()[:200]
        if not fields:
            return JSONResponse(status_code=400, content={"ok": False, "error": "nothing to update"})

        try:
            _upsert_singing_block(fields)
        except Exception as e:
            logger.error(f"[singing] config write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "config write failed"})
        # 热更新运行时配置
        core = await _core()
        core.set_config(fields)
        return JSONResponse({"ok": True, **_singing_config_from_conf(), "restart_required": False})

    @router.get("/api/singing/engine/status")
    async def engine_status(request: Request):
        """so-vits 翻唱引擎探测。"""
        if not _is_local_request(request):
            return _forbidden()
        cfg = _singing_config_from_conf()
        svc_url = str((await _core())._config.get("svc_url") or "")  # 运行时可能已改
        if not svc_url:
            svc_url = str(cfg.get("svc_url") or "")
        return JSONResponse({**await _probe_svc(svc_url), "svc_url": svc_url})

    @router.post("/api/singing/convert")
    async def convert(request: Request):
        """so-vits 整曲转换（P2 基础：转发 flask_api_full_song 契约）。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        core = await _core()
        svc_url = str(core._config.get("svc_url") or "")
        if not svc_url:
            return JSONResponse(status_code=400, content={"ok": False, "error": "so-vits 服务未配置（svc_url）"})
        audio_path = str((body or {}).get("audio_path") or "").strip()
        if not audio_path:
            return JSONResponse(status_code=400, content={"ok": False, "error": "audio_path required"})
        import os  # noqa: PLC0415

        if not os.path.isfile(audio_path):
            return JSONResponse(status_code=400, content={"ok": False, "error": f"文件不存在: {audio_path}"})

        import asyncio  # noqa: PLC0415
        import aiohttp  # noqa: PLC0415

        params = {
            "audio_path": audio_path,
            "tran": int((body or {}).get("tran") or 0),
            "spk": int((body or {}).get("spk") or 0),
            "wav_format": str((body or {}).get("wav_format") or "wav"),
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    svc_url.rstrip("/") + "/wav2wav",
                    data=params,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    if resp.status != 200:
                        return JSONResponse(status_code=502, content={"ok": False, "error": f"so-vits HTTP {resp.status}"})
                    data = await resp.read()
        except Exception as e:
            return JSONResponse(status_code=502, content={"ok": False, "error": f"so-vits 调用失败: {type(e).__name__}"})
        out_path = Path("output") / "singing" / "converted" / f"svc_{int(time.time())}.{params['wav_format']}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return JSONResponse({"ok": True, "audio_url": f"/singing-output/converted/{out_path.name}"})

    return router
