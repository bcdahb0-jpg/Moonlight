"""游戏陪玩 REST 端点（P4）：目标游戏 / 画面事件 / 攻略知识库 / 高光喝彩。

- GET  /api/playmate/games               → 内置游戏表
- GET  /api/playmate/status              → 绑定状态 + 事件流 + 喝彩配置
- POST /api/playmate/bind {game_id?, window_regex?} → 绑定目标游戏（conf）
- POST /api/playmate/analyze {frame}     → 手动分析一帧（复用 screen VisionAnalyzer → 事件分类）
- POST /api/playmate/cheer               → 触发一次喝彩（测试）
- GET  /api/playmate/config / POST ...   → conf system_config.playmate（surgical upsert）
- POST /api/playmate/kb/import {game_id, text, source?} → 导入攻略
- POST /api/playmate/kb/query {game_id, question, top_k} → 检索
- GET  /api/playmate/kb/stats?game_id=   → 库统计

参考：ZerolanLiveRobot（窗口截图 + blip 理解 + 游戏实况）、super-agent-party
（知识库 RAG）；画面捕获复用 screen_awareness（前端 Electron IPC 上报）。
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
from .game import GAMES, get_game, match_game
from .events import classify_event, get_event_broker
from . import kb


async def _broadcast_cheer(recorded: dict) -> None:
    """P5.1 喝彩 TTS 播报：record 返回的 cheer 非空 → bridge 注入台词。

    受 conf playmate.cheer_enabled 开关控制（关闭时只入事件流，不打扰桌宠）。
    """
    cheer = recorded.get("cheer")
    if not cheer:
        return
    try:
        cfg_now = _playmate_config_from_conf()
        if not cfg_now.get("cheer_enabled", True):
            return
    except Exception:
        pass
    try:
        from ..bridge import speak_line  # noqa: PLC0415

        await speak_line(str(cheer), source="playmate")
    except Exception:  # noqa: BLE001 — 桥接失败静默
        pass


DEFAULT_CONFIG = {
    "active_game": "",
    "window_regex": "",
    "sample_sec": 10,
    "vision_enabled": True,
    "cheer_enabled": True,
    "cheer_cooldown": 60,
}


# --------------------------------------------------------------------------- #
# conf.yaml system_config.playmate 块（surgical upsert）
# --------------------------------------------------------------------------- #

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_PLAY_BLOCK_RE = re.compile(r"^(\s*)playmate:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _yaml_render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml_scalar(str(value))


def _upsert_playmate_block(fields: dict) -> bool:
    with open(CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False

    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = _PLAY_BLOCK_RE.match(lines[i])
        if m:
            s, _, e = _find_block_extent(lines, _PLAY_BLOCK_RE, start_from=i)
            found = (s, min(e, sys_end), m.group(1))
            break

    if found is None:
        indent = "  "
        block_lines = [f"{indent}playmate:  # 游戏陪玩（playmate_route，P4）\n"]
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


def _playmate_config_from_conf() -> dict:
    try:
        from ..config_manager.utils import read_yaml  # noqa: PLC0415

        data = read_yaml(CONF_PATH) or {}
        block = ((data.get("system_config", {}) or {}).get("playmate", {}) or {})
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

def init_playmate_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/playmate/games")
    async def games(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse({"ok": True, "games": GAMES})

    @router.get("/api/playmate/status")
    async def status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        cfg = _playmate_config_from_conf()
        broker = get_event_broker()
        return JSONResponse(
            {
                "ok": True,
                "config": cfg,
                "active_game": cfg.get("active_game", ""),
                "window_regex": cfg.get("window_regex", ""),
                "events": broker.recent_events[-10:],
                "kb": {g["id"]: kb.stats(g["id"]) for g in GAMES},
            }
        )

    @router.post("/api/playmate/bind")
    async def bind(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        fields = {}
        game_id = str((body or {}).get("game_id") or "").strip()
        window_regex = str((body or {}).get("window_regex") or "").strip()
        if game_id and not get_game(game_id):
            return JSONResponse(status_code=400, content={"ok": False, "error": f"未知游戏: {game_id}"})
        if window_regex:
            try:
                re.compile(window_regex)
            except re.error as e:
                return JSONResponse(status_code=400, content={"ok": False, "error": f"正则无效: {e}"})
        if game_id:
            fields["active_game"] = game_id
        if body is not None and "window_regex" in body:
            fields["window_regex"] = window_regex
        if not fields:
            return JSONResponse(status_code=400, content={"ok": False, "error": "nothing to bind"})
        try:
            _upsert_playmate_block(fields)
        except Exception as e:
            logger.error(f"[playmate] bind write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "config write failed"})
        return JSONResponse({"ok": True, **_playmate_config_from_conf(), "restart_required": False})

    @router.post("/api/playmate/analyze")
    async def analyze(request: Request):
        """body: ScreenFrame JSON（与 /api/screen/analyze 同结构）。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        cfg = _playmate_config_from_conf()
        try:
            from ..screen_awareness.models import ScreenFrame  # noqa: PLC0415
            from ..screen_awareness.analyzer import VisionAnalyzer  # noqa: PLC0415
            from ..screen_awareness.service import get_store  # noqa: PLC0415

            frame = ScreenFrame.model_validate(body)
            sconfig = get_store().config()
            analyzer = VisionAnalyzer(sconfig)
            snapshot = await analyzer.analyze(frame, api_key=sconfig.api_key)
            if snapshot is None:
                return JSONResponse({"ok": True, "event": None, "reason": "视觉分析失败（provider 不可用？）"})
            game_id = cfg.get("active_game") or "minecraft"
            event = await classify_event(snapshot.summary, game_id)
            recorded = get_event_broker().record(
                {
                    **event,
                    "game_id": game_id,
                    "window": {"title": frame.window.title, "app": frame.window.app},
                }
            )
            await _broadcast_cheer(recorded)
            return JSONResponse({"ok": True, "snapshot_summary": snapshot.summary, "event": recorded})
        except Exception as e:
            logger.warning(f"[playmate] analyze failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=400, content={"ok": False, "error": f"analyze failed: {e}"})

    @router.post("/api/playmate/cheer")
    async def cheer(request: Request):
        """手动触发一次喝彩（测试）。"""
        if not _is_local_request(request):
            return _forbidden()
        recorded = get_event_broker().record(
            {"event_type": "kill", "confidence": 1.0, "summary": "手动测试喝彩", "game_id": "manual"}
        )
        await _broadcast_cheer(recorded)
        return JSONResponse(recorded)

    @router.get("/api/playmate/config")
    async def get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(_playmate_config_from_conf())

    @router.post("/api/playmate/config")
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
            if key in ("active_game", "window_regex"):
                fields[key] = str(value or "").strip()[:128]
            elif key == "sample_sec":
                try:
                    fields[key] = max(3, min(60, int(value)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": "sample_sec must be int"})
            elif key == "cheer_cooldown":
                try:
                    fields[key] = max(10, min(600, int(value)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"ok": False, "error": "cheer_cooldown must be int"})
            elif key in ("vision_enabled", "cheer_enabled"):
                fields[key] = bool(value)
        if not fields:
            return JSONResponse(status_code=400, content={"ok": False, "error": "nothing to update"})
        try:
            _upsert_playmate_block(fields)
        except Exception as e:
            logger.error(f"[playmate] config write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "config write failed"})
        # 热更新喝彩冷却
        if "cheer_cooldown" in fields:
            get_event_broker().set_cooldown(float(fields["cheer_cooldown"]))
        return JSONResponse({"ok": True, **_playmate_config_from_conf(), "restart_required": False})

    # ------------------------------------------------------------------ //
    # 攻略知识库
    # ------------------------------------------------------------------ //
    @router.post("/api/playmate/kb/import")
    async def kb_import(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        game_id = str((body or {}).get("game_id") or "").strip()
        text = str((body or {}).get("text") or "").strip()
        source = str((body or {}).get("source") or "").strip()[:64]
        if not game_id or not get_game(game_id):
            return JSONResponse(status_code=400, content={"ok": False, "error": "game_id required / unknown"})
        if not text:
            return JSONResponse(status_code=400, content={"ok": False, "error": "text required"})
        return JSONResponse(await kb.import_text(game_id, text, source))

    @router.post("/api/playmate/kb/query")
    async def kb_query(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        game_id = str((body or {}).get("game_id") or "").strip()
        question = str((body or {}).get("question") or "").strip()
        top_k = int((body or {}).get("top_k") or 3)
        if not game_id or not question:
            return JSONResponse(status_code=400, content={"ok": False, "error": "game_id & question required"})
        return JSONResponse(await kb.query(game_id, question, top_k))

    @router.get("/api/playmate/kb/stats")
    async def kb_stats(request: Request, game_id: str = ""):
        if not _is_local_request(request):
            return _forbidden()
        if game_id:
            return JSONResponse(kb.stats(game_id))
        return JSONResponse({"ok": True, "stats": {g["id"]: kb.stats(g["id"]) for g in GAMES}})

    return router
