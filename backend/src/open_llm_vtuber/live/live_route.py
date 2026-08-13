"""直播与互动 REST 端点（P3）：B站接入 / 弹幕玩法 / 叠加层 / OBS+VTS。

- GET  /api/live/status                  → 直播间状态（监听中/弹幕数/Cookie 完整性）
- POST /api/live/connect {room_id, cookies?, reply_mode?} → 连接（Cookie 缺失时仅监听）
- POST /api/live/disconnect              → 断开
- POST /api/live/danmaku {text}          → 手动发弹幕（测试）
- GET  /api/live/config / POST ...       → conf system_config.live（surgical upsert + 热更新）
- GET  /api/live/overlay/chat            → 弹幕流（叠加层轮询）
- GET  /api/live/overlay/songlist        → 唱歌队列（叠加层歌单）
- GET  /api/live/obs/status / POST /api/live/obs/{play_video|control_video|change_scene|show_text}
- GET  /api/live/vts/status / POST /api/live/vts/{emote|swing|stop}

参考：ZerolanLiveRobot bilibili/service.py（Credential 三件套 + 弹幕监听）、
AI-YinMei func/obs/obs_websocket.py + func/vtuber/（OBS/VTS 控制）
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from ..llm_config_route import _is_local_request, _forbidden
from ..translator_route import _find_block_extent, _backup_once, _atomic_write, _quote_yaml_scalar, CONF_PATH
from .bili_live import get_live_manager
from .danmaku_dispatch import get_dispatcher
from .obs_control import get_obs_controller, reconfigure_obs
from .vts_control import get_vts_controller, reconfigure_vts

DEFAULT_CONFIG = {
    "room_id": 0,
    "sessdata": "",
    "bili_jct": "",
    "buvid3": "",
    "reply_mode": "danmaku",  # danmaku | voice+danmaku | voice
    "welcome_enabled": True,
    "gift_enabled": True,
    "chat_to_conversation": True,  # P5.1 弹幕闲聊 → 桌宠主对话（bridge）
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "obs_password": "",
    "obs_scene": "",
    "vts_host": "127.0.0.1",
    "vts_port": 8001,
}

# 敏感字段（读取时打码，绝不回显明文）
_SECRET_KEYS = ("sessdata", "bili_jct", "buvid3", "obs_password")


def _mask(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}****" if len(value) > 6 else "****"


# --------------------------------------------------------------------------- #
# conf.yaml system_config.live 块（surgical upsert）
# --------------------------------------------------------------------------- #

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_LIVE_BLOCK_RE = re.compile(r"^(\s*)live:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _yaml_render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml_scalar(str(value))


def _upsert_live_block(fields: dict) -> bool:
    with open(CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False

    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = _LIVE_BLOCK_RE.match(lines[i])
        if m:
            s, _, e = _find_block_extent(lines, _LIVE_BLOCK_RE, start_from=i)
            found = (s, min(e, sys_end), m.group(1))
            break

    if found is None:
        indent = "  "
        block_lines = [f"{indent}live:  # 直播与互动（live_route，P3）\n"]
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


def _live_config_from_conf() -> dict:
    try:
        from ..config_manager.utils import read_yaml  # noqa: PLC0415

        data = read_yaml(CONF_PATH) or {}
        block = ((data.get("system_config", {}) or {}).get("live", {}) or {})
        out = dict(DEFAULT_CONFIG)
        for key in DEFAULT_CONFIG:
            if key in block and block[key] is not None:
                out[key] = block[key]
        return out
    except Exception:
        return dict(DEFAULT_CONFIG)


def _public_config(cfg: dict) -> dict:
    """打码敏感字段后的配置（前端展示用）。"""
    out = dict(cfg)
    for key in _SECRET_KEYS:
        out[key] = _mask(str(out.get(key) or ""))
    return out


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #

def init_live_route() -> APIRouter:
    router = APIRouter()

    # P5.1 弹幕闲聊进对话：普通闲聊弹幕 → bridge 注入主对话（AI 回复 + TTS）。
    # 受 conf live.chat_to_conversation 开关控制（关闭时只入叠加层流，不打扰桌宠）。
    # 目标连接忙/无连接时静默跳过（桥接不打扰正在进行的回复）。
    try:
        from ..bridge import feed_as_user_input  # noqa: PLC0415

        async def _chat_sink(parsed: dict) -> None:
            try:
                cfg_now = _live_config_from_conf()
                if not cfg_now.get("chat_to_conversation", True):
                    return
            except Exception:
                pass
            text = f"【直播间弹幕 {parsed.get('uname', '观众')}】{parsed.get('text', '')}"
            await feed_as_user_input(text, source="danmaku")

        get_dispatcher().set_chat_sink(_chat_sink)
    except Exception as _e:
        logger.warning(f"[live] chat_sink 注册失败: {_e}")

    def _apply_runtime(cfg: dict) -> None:
        """把配置热更新到运行时单例。"""
        if cfg.get("obs_host"):
            try:
                reconfigure_obs(str(cfg["obs_host"]), int(cfg.get("obs_port") or 4455), str(cfg.get("obs_password") or ""))
            except Exception:
                pass
        if cfg.get("vts_host"):
            try:
                reconfigure_vts(str(cfg["vts_host"]), int(cfg.get("vts_port") or 8001))
            except Exception:
                pass

    @router.get("/api/live/status")
    async def live_status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(get_live_manager().status)

    @router.post("/api/live/connect")
    async def live_connect(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        cfg = _live_config_from_conf()
        room_id = int(body.get("room_id") or cfg.get("room_id") or 0)
        if room_id <= 0:
            return JSONResponse(status_code=400, content={"ok": False, "error": "room_id required"})
        cookies = {
            "sessdata": str(body.get("sessdata") or cfg.get("sessdata") or ""),
            "bili_jct": str(body.get("bili_jct") or cfg.get("bili_jct") or ""),
            "buvid3": str(body.get("buvid3") or cfg.get("buvid3") or ""),
        }
        reply_mode = str(body.get("reply_mode") or cfg.get("reply_mode") or "danmaku")
        result = await get_live_manager().connect(room_id, cookies, reply_mode)
        if result["ok"]:
            # 落盘配置（供下次启动恢复）
            try:
                _upsert_live_block(
                    {
                        "room_id": room_id,
                        **{k: v for k, v in cookies.items() if v},
                        "reply_mode": reply_mode,
                    }
                )
            except Exception:
                pass
        return JSONResponse({**result, "status": get_live_manager().status})

    @router.post("/api/live/disconnect")
    async def live_disconnect(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        await get_live_manager().disconnect()
        return JSONResponse({"ok": True})

    @router.post("/api/live/danmaku")
    async def live_send_danmaku(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        text = str((body or {}).get("text") or "").strip()[:100]
        if not text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "text required"})
        return JSONResponse(await get_live_manager().send_danmaku(text))

    @router.get("/api/live/config")
    async def live_get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(_public_config(_live_config_from_conf()))

    @router.post("/api/live/config")
    async def live_set_config(request: Request):
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
            if key == "room_id":
                try:
                    fields[key] = max(0, int(value))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": "room_id must be int"})
            elif key in _SECRET_KEYS:
                v = str(value or "").strip()
                if v and not v.startswith("****"):
                    fields[key] = v  # 前端回显占位（**** 开头）不覆盖
            elif key in ("reply_mode", "obs_scene"):
                fields[key] = str(value or "").strip()[:64]
            elif key == "obs_host":
                fields[key] = str(value or "").strip()[:128]
            elif key == "obs_port":
                try:
                    fields[key] = max(1, min(65535, int(value)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": "obs_port must be int"})
            elif key == "vts_host":
                fields[key] = str(value or "").strip()[:128]
            elif key == "vts_port":
                try:
                    fields[key] = max(1, min(65535, int(value)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": "vts_port must be int"})
            elif key in ("welcome_enabled", "gift_enabled"):
                fields[key] = bool(value)
        if not fields:
            return JSONResponse(status_code=400, content={"ok": False, "error": "nothing to update"})

        try:
            _upsert_live_block(fields)
        except Exception as e:
            logger.error(f"[live] config write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "config write failed"})
        merged = {**DEFAULT_CONFIG, **fields}
        _apply_runtime(merged)
        return JSONResponse({"ok": True, **_public_config(merged), "restart_required": False})

    # ------------------------------------------------------------------ //
    # 叠加层（OBS 浏览器源轮询）
    # ------------------------------------------------------------------ //
    @router.get("/api/live/overlay/chat")
    async def overlay_chat(request: Request, limit: int = 30):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(
            {
                "ok": True,
                "messages": get_dispatcher().recent(max(1, min(100, limit))),
                "ts": time.time(),
            }
        )

    @router.get("/api/live/overlay/songlist")
    async def overlay_songlist(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        from ..singing.sing_core import get_sing_core  # noqa: PLC0415

        try:
            core = await get_sing_core()
            status = core.status()
        except Exception:
            status = {"queue": [], "ready": [], "current": None}
        return JSONResponse(
            {
                "ok": True,
                "queue": status.get("queue", []),
                "ready": [s.get("songname") for s in status.get("ready", [])],
                "current": (status.get("current") or {}).get("songname"),
                "ts": time.time(),
            }
        )

    # ------------------------------------------------------------------ //
    # OBS / VTS 控制
    # ------------------------------------------------------------------ //
    @router.get("/api/live/obs/status")
    async def obs_status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(get_obs_controller().status())

    @router.post("/api/live/obs/{action}")
    async def obs_action(request: Request, action: str):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            body = {}
        obs = get_obs_controller()
        if action == "play_video":
            return JSONResponse(obs.play_video(str(body.get("input") or "视频"), str(body.get("file") or "")))
        if action == "control_video":
            return JSONResponse(obs.control_video(str(body.get("input") or "视频"), str(body.get("action") or "PLAY")))
        if action == "change_scene":
            return JSONResponse(obs.change_scene(str(body.get("scene") or "")))
        if action == "show_text":
            return JSONResponse(obs.show_text(str(body.get("input") or "文本"), str(body.get("text") or "")))
        return JSONResponse(status_code=400, content={"ok": False, "error": f"unknown action: {action}"})

    @router.get("/api/live/vts/status")
    async def vts_status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(await get_vts_controller().status())

    @router.post("/api/live/vts/{action}")
    async def vts_action(request: Request, action: str):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            body = {}
        vts = get_vts_controller()
        if action == "emote":
            return JSONResponse(await vts.trigger_emote(str(body.get("hotkey") or "")))
        if action == "swing":
            return JSONResponse(await vts.swing(True))
        if action == "stop":
            return JSONResponse(await vts.swing(False))
        return JSONResponse(status_code=400, content={"ok": False, "error": f"unknown action: {action}"})

    return router
