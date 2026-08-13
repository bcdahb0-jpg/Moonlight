"""OBS WebSocket 控制（P3）。

依赖 `obs-websocket-py`（obswebsocket 包）——未安装时 fail-soft：所有接口
返回「未安装依赖」错误，装完 `uv add obs-websocket-py` 即启用。
封装（参考 AI-YinMei func/obs/obs_websocket.py）：
- play_video(input_name, file)      → 换媒体源本地文件
- control_video(input_name, action) → PLAY / PAUSE / STOP / RESTART
- change_scene(name)                → 切场景
- show_text(input_name, text)       → 文本源更新
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

try:
    from obswebsocket import obsws, requests as obs_requests  # type: ignore

    OBS_AVAILABLE = True
except ImportError:
    OBS_AVAILABLE = False


class ObsController:
    def __init__(self, host: str = "127.0.0.1", port: int = 4455, password: str = "") -> None:
        self._host = host
        self._port = int(port)
        self._password = password or ""
        self._ws: Optional[Any] = None

    @property
    def available(self) -> bool:
        return OBS_AVAILABLE

    def _connect(self) -> Optional[Any]:
        if not OBS_AVAILABLE:
            return None
        if self._ws is not None:
            return self._ws
        try:
            ws = obsws(self._host, self._port, self._password)
            ws.connect()
            self._ws = ws
            return ws
        except Exception as e:
            logger.warning(f"[obs] connect failed: {type(e).__name__}: {e}")
            return None

    def _call(self, request) -> Optional[Any]:
        ws = self._connect()
        if ws is None:
            return None
        try:
            return ws.call(request)
        except Exception as e:
            logger.warning(f"[obs] request failed: {type(e).__name__}: {e}")
            try:
                ws.disconnect()
            except Exception:
                pass
            self._ws = None
            return None

    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        if not OBS_AVAILABLE:
            return {"available": False, "error": "obs-websocket-py 未安装（uv add obs-websocket-py）", "online": False}
        ws = self._connect()
        online = ws is not None
        scenes = []
        if online:
            resp = self._call(obs_requests.GetSceneList())
            if resp:
                scenes = [s.get("sceneName") or s.get("name") for s in (resp.getScenes() or [])]
        return {"available": True, "online": online, "scenes": scenes, "host": self._host, "port": self._port}

    def play_video(self, input_name: str, file_path: str) -> dict:
        if not OBS_AVAILABLE:
            return {"ok": False, "error": "obswebsocket 未安装"}
        resp = self._call(
            obs_requests.SetInputSettings(inputName=input_name, settings={"local_file": file_path})
        )
        return {"ok": resp is not None, "action": "play_video", "input": input_name}

    def control_video(self, input_name: str, action: str) -> dict:
        if not OBS_AVAILABLE:
            return {"ok": False, "error": "obswebsocket 未安装"}
        action = (action or "PLAY").upper()
        if action not in ("PLAY", "PAUSE", "STOP", "RESTART"):
            return {"ok": False, "error": f"action must be PLAY|PAUSE|STOP|RESTART, got {action}"}
        resp = self._call(obs_requests.TriggerMediaInputAction(inputName=input_name, mediaAction=action))
        return {"ok": resp is not None, "action": action, "input": input_name}

    def change_scene(self, scene_name: str) -> dict:
        if not OBS_AVAILABLE:
            return {"ok": False, "error": "obswebsocket 未安装"}
        resp = self._call(obs_requests.SetCurrentProgramScene(sceneName=scene_name))
        return {"ok": resp is not None, "scene": scene_name}

    def show_text(self, input_name: str, text: str) -> dict:
        if not OBS_AVAILABLE:
            return {"ok": False, "error": "obswebsocket 未安装"}
        resp = self._call(obs_requests.SetInputSettings(inputName=input_name, settings={"text": text}))
        return {"ok": resp is not None, "input": input_name}


# 全局单例（配置在 live_route 初始化时注入）
_obs: Optional[ObsController] = None


def get_obs_controller() -> ObsController:
    global _obs
    if _obs is None:
        _obs = ObsController()
    return _obs


def reconfigure_obs(host: str, port: int, password: str = "") -> None:
    global _obs
    _obs = ObsController(host, port, password)
