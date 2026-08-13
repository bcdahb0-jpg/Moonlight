"""B站直播客户端（P3）：bilibili-api-python 监听 + 发弹幕。

统一使用 bilibili-api-python（ZerolanLiveRobot 同款）：
- 监听：live.LiveDanmaku 事件（DANMU_MSG / INTERACT_WORD / SEND_GIFT）
  → 路由到 DanmakuDispatcher（点歌/切歌/欢迎/礼物/弹幕流）
- 发送：live.LiveRoom.send_danmaku（内部处理 wbi 签名）

依赖缺失时 fail-soft：status 明确返回 can_listen/can_send=false。
"""
from __future__ import annotations

import asyncio
import http.cookies
from typing import Any, Optional

from loguru import logger
import aiohttp

from .danmaku_dispatch import get_dispatcher

try:
    from bilibili_api import live as bili_live
    from bilibili_api import Credential

    BILIAPI_AVAILABLE = True
except ImportError:
    BILIAPI_AVAILABLE = False


class BiliLiveClient:
    """单个直播间客户端（监听 + 发送）。"""

    def __init__(
        self,
        room_id: int,
        sessdata: str = "",
        bili_jct: str = "",
        buvid3: str = "",
    ) -> None:
        self.room_id = int(room_id)
        self._sessdata = sessdata
        self._bili_jct = bili_jct
        self._buvid3 = buvid3
        self._session: Optional[aiohttp.ClientSession] = None
        self._danmaku: Optional[Any] = None
        self._sender: Optional[Any] = None
        self._running = False

    # ------------------------------------------------------------------ #
    @property
    def can_listen(self) -> bool:
        return BILIAPI_AVAILABLE

    @property
    def can_send(self) -> bool:
        return BILIAPI_AVAILABLE and bool(self._sessdata and self._bili_jct and self._buvid3)

    @property
    def listening(self) -> bool:
        return self._running and self._danmaku is not None

    def _credential(self):
        if not (self._sessdata or self._bili_jct or self._buvid3):
            return None
        return Credential(
            sessdata=self._sessdata,
            bili_jct=self._bili_jct,
            buvid3=self._buvid3,
        )

    # ------------------------------------------------------------------ #
    async def start_listening(self) -> None:
        """启动 LiveDanmaku 监听（事件注册 + connect）。"""
        if not BILIAPI_AVAILABLE:
            raise RuntimeError("bilibili-api-python 未安装，无法监听弹幕")
        self._danmaku = bili_live.LiveDanmaku(self.room_id, credential=self._credential())

        @self._danmaku.on("DANMU_MSG")
        async def on_danmaku(event: dict):
            try:
                info = event["data"]["info"]
                msg = str(info[1])
                uname = str(info[2][1])
                uid = str(info[2][0])
                await get_dispatcher().on_danmaku(msg, uname=uname, uid=uid)
            except Exception as e:
                logger.debug(f"[live] danmaku event failed: {e}")

        @self._danmaku.on("INTERACT_WORD")
        async def on_interact(event: dict):
            try:
                data = event["data"]["data"]
                uname = str(data.get("uname") or "观众")
                uid = str(data.get("uid") or "")
                await get_dispatcher().on_interact(uname, uid=uid)
            except Exception as e:
                logger.debug(f"[live] interact event failed: {e}")

        @self._danmaku.on("SEND_GIFT")
        async def on_gift(event: dict):
            try:
                data = event["data"]["data"]
                uname = str(data.get("uname") or "观众")
                gift_name = str(data.get("giftName") or "礼物")
                num = int(data.get("num") or 1)
                uid = str(data.get("uid") or "")
                await get_dispatcher().on_gift(uname, gift_name, num, uid=uid)
            except Exception as e:
                logger.debug(f"[live] gift event failed: {e}")

        await self._danmaku.connect()
        self._running = True
        logger.info(f"[live] 开始监听直播间 {self.room_id}")

    async def stop_listening(self) -> None:
        self._running = False
        if self._danmaku:
            try:
                await self._danmaku.disconnect()
            except Exception:
                pass
            self._danmaku = None

    # ------------------------------------------------------------------ #
    async def send_danmaku(self, text: str) -> bool:
        """发弹幕（LiveRoom.send_danmaku）。"""
        if not self.can_send:
            logger.warning("[live] 发弹幕不可用：Cookie 三件套不全")
            return False
        if self._sender is None:
            self._sender = bili_live.LiveRoom(self.room_id, credential=self._credential())
        try:
            await self._sender.send_danmaku(text)
            return True
        except Exception as e:
            logger.warning(f"[live] 发弹幕失败: {type(e).__name__}: {e}")
            return False


# --------------------------------------------------------------------------- #
# LiveManager：房间生命周期 + 状态
# --------------------------------------------------------------------------- #

class LiveManager:
    """管理当前直播间客户端与状态（全局单例）。"""

    def __init__(self) -> None:
        self._client: Optional[BiliLiveClient] = None
        self._room_id: int = 0
        self._cookies: dict[str, str] = {}
        self._reply_mode = "danmaku"  # danmaku | voice+danmaku | voice
        self._danmaku_count = 0

    # ------------------------------------------------------------------ #
    @property
    def status(self) -> dict:
        listening = bool(self._client and self._client.listening)
        return {
            "room_id": self._room_id,
            "listening": listening,
            "danmaku_count": self._danmaku_count,
            "can_listen": BILIAPI_AVAILABLE,
            "can_send": bool(self._client and self._client.can_send),
            "biliapi_available": BILIAPI_AVAILABLE,
            "reply_mode": self._reply_mode,
            "login": {
                "sessdata": bool(self._cookies.get("sessdata")),
                "bili_jct": bool(self._cookies.get("bili_jct")),
                "buvid3": bool(self._cookies.get("buvid3")),
            },
        }

    async def connect(self, room_id: int, cookies: dict[str, str], reply_mode: str = "danmaku") -> dict:
        """连接直播间（先断开旧的）。"""
        await self.disconnect()
        self._room_id = int(room_id)
        self._cookies = dict(cookies or {})
        self._reply_mode = reply_mode or "danmaku"
        client = BiliLiveClient(
            self._room_id,
            sessdata=self._cookies.get("sessdata", ""),
            bili_jct=self._cookies.get("bili_jct", ""),
            buvid3=self._cookies.get("buvid3", ""),
        )
        try:
            await client.start_listening()
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        self._client = client
        logger.info(f"[live] connected room={self._room_id}")
        return {"ok": True}

    async def disconnect(self) -> None:
        if self._client:
            await self._client.stop_listening()
            self._client = None
        self._danmaku_count = 0

    async def send_danmaku(self, text: str) -> dict:
        if not self._client:
            return {"ok": False, "error": "未连接直播间"}
        ok = await self._client.send_danmaku(text)
        return {"ok": ok, "error": None if ok else "发送失败（Cookie 不全或风控）"}


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #
_manager: Optional[LiveManager] = None


def get_live_manager() -> LiveManager:
    global _manager
    if _manager is None:
        _manager = LiveManager()
    return _manager
