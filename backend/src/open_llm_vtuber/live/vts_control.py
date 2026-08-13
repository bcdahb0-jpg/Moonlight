"""VTube Studio WebSocket 控制（P3）：表情热键 + 摇摆动作。

依赖 websockets（已在项目依赖）。协议参考 AI-YinMei func/vtuber/ 与
VTube Studio Plugin API（AuthenticationTokenRequest → emote/action）。
简化实现：仅做「鉴权 + 触发动作/表情」两个基础调用（API 版本 1.0）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import websockets
from loguru import logger


class VtsController:
    def __init__(self, host: str = "127.0.0.1", port: int = 8001, plugin_name: str = "Moonlight") -> None:
        self._url = f"ws://{host}:{port}"
        self._plugin_name = plugin_name
        self._ws: Optional[Any] = None

    async def _connect(self) -> Optional[Any]:
        if self._ws is not None:
            return self._ws
        try:
            ws = await websockets.connect(self._url, ping_interval=20, ping_timeout=10, close_timeout=5)
            # 鉴权（VTS 会弹出确认；API v1.0 需要 pluginDeveloper 声明）
            auth = {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "auth-1",
                "messageType": "AuthenticationRequest",
                "data": {
                    "pluginName": self._plugin_name,
                    "pluginDeveloper": "Moonlight",
                    "authenticationToken": "",
                },
            }
            await ws.send(json.dumps(auth))
            raw = await ws.recv()
            data = json.loads(raw)
            if data.get("data", {}).get("authenticated"):
                self._ws = ws
                return ws
            # 未鉴权：VTS 端会弹窗确认，静默关闭连接（下次调用重试）
            logger.info(f"[vts] auth pending: {data.get('data', {}).get('reason', '')}")
            await ws.close()
            return None
        except Exception as e:
            logger.warning(f"[vts] connect failed: {type(e).__name__}: {e}")
            return None

    async def _request(self, message_type: str, data: dict, request_id: str = "req") -> Optional[dict]:
        ws = await self._connect()
        if ws is None:
            return None
        try:
            await ws.send(
                json.dumps(
                    {
                        "apiName": "VTubeStudioPublicAPI",
                        "apiVersion": "1.0",
                        "requestID": request_id,
                        "messageType": message_type,
                        "data": data,
                    }
                )
            )
            raw = await ws.recv()
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[vts] request {message_type} failed: {type(e).__name__}")
            try:
                await ws.close()
            except Exception:
                pass
            self._ws = None
            return None

    # ------------------------------------------------------------------ #
    async def status(self) -> dict:
        resp = await self._request("APIStateRequest", {}, "state")
        online = resp is not None
        return {"online": online, "url": self._url, "detail": resp.get("data") if resp else None}

    async def trigger_emote(self, hotkey: str) -> dict:
        """触发表情热键（VTS 设置里的 hotkey 名，如「开心」「砸礼物」）。"""
        resp = await self._request(
            "HotkeyTriggerRequest",
            {"hotkeyID": "", "hotkey": hotkey, "triggerOn": True},
            f"emote-{hotkey[:16]}",
        )
        return {"ok": resp is not None, "action": "emote", "hotkey": hotkey}

    async def swing(self, on: bool = True) -> dict:
        """循环摇摆/静止（动作：AutoSwing / 停止发送「静止」）。"""
        action = "AutoSwing" if on else "静止"
        resp = await self._request(
            "ActionRequest",
            {"actionName": action, "actionID": ""},
            "swing" if on else "stop",
        )
        return {"ok": resp is not None, "action": action}


# 全局单例
_vts: Optional[VtsController] = None


def get_vts_controller() -> VtsController:
    global _vts
    if _vts is None:
        _vts = VtsController()
    return _vts


def reconfigure_vts(host: str, port: int) -> None:
    global _vts
    _vts = VtsController(host, port)
