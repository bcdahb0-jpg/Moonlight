"""social/qq_client.py — QQ 社交连接器（P6，PetGPT socialAgent 思路）。

OneBot v11（NapCat）WebSocket 客户端：
- 连 `ws://127.0.0.1:3001`（NapCat WebSocket 服务器默认端口，可配置）；
- 收到 message（group/private）→ 提取文本 → `bridge.feed_as_user_input`
  注入主对话（AI 回复 + TTS 出声，桌宠「接管」QQ 聊天）；
- 回复回发：`send_msg` 动作（用户可关闭回发，只本地对话）；
- 断线自动重连（3s 间隔，指数退避到 30s）；消息计数供前端展示。

风控提示：个人使用自担风险（QQ 协议第三方实现可能触发账号风控）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from loguru import logger


class QQClient:
    """单连接 OneBot v11 客户端（asyncio 后台任务驱动）。"""

    def __init__(self) -> None:
        self.ws_url: str = "ws://127.0.0.1:3001"
        self.enabled: bool = False
        self.auto_reply: bool = True  # 是否回发 QQ 消息
        self._task: Optional[asyncio.Task] = None
        self._connected = False
        self._msg_count = 0
        self._last_error = ""
        self._last_event_at: Optional[float] = None
        self._ws: Any = None  # websockets connection
        self._pending_reply: Optional[dict] = None  # {message_type, target, text}

    # ---- 状态 ----
    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "url": self.ws_url,
            "auto_reply": self.auto_reply,
            "msg_count": self._msg_count,
            "last_error": self._last_error,
            "last_event_at": self._last_event_at,
        }

    def configure(self, enabled: bool, ws_url: str, auto_reply: bool = True) -> None:
        self.enabled = enabled
        self.ws_url = (ws_url or "ws://127.0.0.1:3001").strip()
        self.auto_reply = auto_reply
        if not enabled:
            asyncio.get_event_loop().create_task(self.stop())
        elif self._task is None or self._task.done():
            asyncio.get_event_loop().create_task(self.start())

    # ---- 生命周期 ----
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def _run(self) -> None:
        """重连循环（3s → 30s 指数退避）。"""
        delay = 3.0
        while self.enabled:
            try:
                await self._connect_once()
                delay = 3.0
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self._connected = False
                self._last_error = str(e)[:120]
                logger.warning(f"[qq] 连接失败: {e}（{delay}s 后重试）")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break
                delay = min(delay * 1.5, 30.0)

    async def _connect_once(self) -> None:
        import websockets  # noqa: PLC0415

        self._last_error = ""
        async with websockets.connect(
            self.ws_url, ping_interval=20, ping_timeout=20, max_size=4 * 1024 * 1024
        ) as ws:
            self._ws = ws
            self._connected = True
            logger.info(f"[qq] 已连接 {self.ws_url}")
            try:
                async for raw in ws:
                    self._last_event_at = time.time()
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_event(event)
            finally:
                self._connected = False
                self._ws = None

    # ---- 事件处理 ----
    async def _handle_event(self, event: dict) -> None:
        post_type = event.get("post_type")
        if post_type != "message":
            return  # 心跳/通知等忽略
        message_type = event.get("message_type")  # group | private
        user_id = event.get("user_id")
        nickname = ((event.get("sender") or {}).get("nickname")) or f"QQ{user_id}"
        text = _extract_text(event.get("message"))
        if not text:
            return
        self._msg_count += 1

        # 注入主对话（fail-soft：桌宠离线时静默）
        try:
            from ..bridge import feed_as_user_input  # noqa: PLC0415

            source = "qq-group" if message_type == "group" else "qq-private"
            await feed_as_user_input(f"【QQ·{nickname}】{text}", source=source)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qq] 对话注入失败: {e}")

        # 回发标记（bridge 无回传机制；MVP 先记录，回发由 AI 回复链路后置）
        if self.auto_reply:
            self._pending_reply = {
                "message_type": message_type,
                "target": event.get("group_id") or user_id,
                "text": text,
            }

    async def send_text(self, text: str, message_type: str = "group", target: Any = None) -> bool:
        """主动回发消息（OneBot send_msg 动作）。"""
        if not self._connected or self._ws is None:
            return False
        payload = {
            "action": "send_msg",
            "params": {
                "message_type": message_type,
                "message": text,
            },
        }
        if message_type == "group":
            payload["params"]["group_id"] = int(target or 0)
        else:
            payload["params"]["user_id"] = int(target or 0)
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qq] 回发失败: {e}")
            return False


def _extract_text(message: Any) -> str:
    """OneBot message 数组 → 纯文本（只取 type=text）。"""
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts = [m.get("data", {}).get("text", "") for m in message if isinstance(m, dict) and m.get("type") == "text"]
        return "".join(parts).strip()
    return ""


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #

_client: Optional[QQClient] = None


def get_qq_client() -> QQClient:
    global _client
    if _client is None:
        _client = QQClient()
    return _client


__all__: list[str] = ["QQClient", "get_qq_client"]
