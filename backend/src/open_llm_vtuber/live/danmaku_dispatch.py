"""弹幕玩法分发（P3）：指令识别 + 欢迎/礼物模板 + 弹幕流。

- `parse_danmaku(text)`：识别指令（唱歌/切歌/点歌等，复用 singing 触发词）
- `DanmakuDispatcher`：持有弹幕流（deque，供叠加层轮询）+ 指令回调注册；
  欢迎（INTERACT_WORD）/礼物（Gift）事件去重后生成模板文案入流。

参考：reference/AI-YinMei/func/cmd/cmd_core.py（指令分派）+ entranceCore
（进房欢迎 / 礼物 TTL 去重）
"""
from __future__ import annotations

import time
from collections import deque
from typing import Awaitable, Callable, Optional

from ..singing.sing_core import extract_sing_request

# 弹幕指令类型（不依赖具体平台）
DANMAKU_INTENT_CHAT = "chat"        # 普通闲聊（入流）
DANMAKU_INTENT_SING = "sing"        # 点歌
DANMAKU_INTENT_NEXT = "next"        # 切歌
DANMAKU_INTENT_STOP = "stop_learning"  # 停止学歌
DANMAKU_INTENT_CLEAR = "clear"      # 清空队列
DANMAKU_INTENT_WELCOME = "welcome"  # 进房欢迎（事件触发）
DANMAKU_INTENT_GIFT = "gift"        # 礼物感谢（事件触发）

# 切歌/清空等控制指令（弹幕输入"切歌"/"下一首"/"清空队列"等）
_CONTROL_MAP = {
    "切歌": DANMAKU_INTENT_NEXT,
    "下一首": DANMAKU_INTENT_NEXT,
    "stop": DANMAKU_INTENT_STOP,
    "停止学歌": DANMAKU_INTENT_STOP,
    "清空队列": DANMAKU_INTENT_CLEAR,
    "清空": DANMAKU_INTENT_CLEAR,
}

_WELCOME_TEMPLATES = (
    "欢迎 {uname} 进入直播间～",
    "{uname} 来啦！欢迎欢迎～",
    "欢迎新朋友 {uname}！",
)
_GIFT_TEMPLATES = (
    "感谢 {uname} 送的 {gift_name}×{num}！",
    "哇，{uname} 送了 {gift_name}，谢谢老板！",
    "{gift_name}×{num} 收到！谢谢 {uname}～",
)

# 礼物/欢迎去重窗口（秒，TTL 内同 uname 只播一次）。
_DEDUP_TTL_SEC = 10.0


def parse_danmaku(text: str) -> dict:
    """解析弹幕 → {intent, songname?, text}。"""
    stripped = (text or "").strip()
    if not stripped:
        return {"intent": DANMAKU_INTENT_CHAT, "text": stripped}

    # 控制指令（全等匹配）
    lower = stripped.lower()
    if lower in _CONTROL_MAP:
        return {"intent": _CONTROL_MAP[lower], "text": stripped}
    # 唱歌指令（触发词表复用 singing）
    songname = extract_sing_request(stripped)
    if songname:
        return {"intent": DANMAKU_INTENT_SING, "songname": songname, "text": stripped}

    return {"intent": DANMAKU_INTENT_CHAT, "text": stripped}


class DanmakuDispatcher:
    """弹幕流 + 指令分发（全局单例，线程安全由 asyncio 保证）。"""

    def __init__(self, maxlen: int = 200) -> None:
        self._stream: deque[dict] = deque(maxlen=maxlen)
        self._last_announce: dict[str, float] = {}  # uname/事件 → 时间（去重）
        # 指令回调：intent → async fn(payload) -> 回执文案 | None
        self._handlers: dict[str, Callable[[dict], Awaitable[Optional[str]]]] = {}
        self._chat_sink: Optional[Callable[[dict], Awaitable[None]]] = None

    # ------------------------------------------------------------------ #
    def register_handler(self, intent: str, handler: Callable[[dict], Awaitable[Optional[str]]]) -> None:
        self._handlers[intent] = handler

    def set_chat_sink(self, sink: Callable[[dict], Awaitable[None]]) -> None:
        """普通闲聊弹幕的消费出口（如进叠加层/主对话）。"""
        self._chat_sink = sink

    # ------------------------------------------------------------------ #
    async def on_danmaku(self, text: str, uname: str = "", uid: str = "") -> None:
        """收到一条弹幕：解析 → 分发 → 回执入流。"""
        parsed = parse_danmaku(text)
        parsed["uname"] = uname or "观众"
        parsed["uid"] = uid
        parsed["ts"] = time.time()

        if parsed["intent"] != DANMAKU_INTENT_CHAT:
            handler = self._handlers.get(parsed["intent"])
            if handler:
                try:
                    reply = await handler(parsed)
                except Exception:
                    reply = None
                if reply:
                    self.push("system", reply, uname="")
                return
        # 普通聊天 → 入流 + chat_sink（叠加层显示）
        self.push(DANMAKU_INTENT_CHAT, text, uname=uname, uid=uid)
        if self._chat_sink:
            try:
                await self._chat_sink(parsed)
            except Exception:
                pass

    async def on_interact(self, uname: str, uid: str = "") -> None:
        """进房事件（INTERACT_WORD）。"""
        key = f"welcome:{uid or uname}"
        if self._dedup(key):
            return
        import random

        text = random.choice(_WELCOME_TEMPLATES).format(uname=uname)
        self.push(DANMAKU_INTENT_WELCOME, text, uname=uname, uid=uid)

    async def on_gift(self, uname: str, gift_name: str, num: int, uid: str = "") -> None:
        """礼物事件。"""
        key = f"gift:{uid or uname}:{gift_name}"
        if self._dedup(key):
            return
        import random

        text = random.choice(_GIFT_TEMPLATES).format(uname=uname, gift_name=gift_name, num=num)
        self.push(DANMAKU_INTENT_GIFT, text, uname=uname, uid=uid)

    # ------------------------------------------------------------------ #
    def push(self, kind: str, text: str, uname: str = "", uid: str = "") -> None:
        """入弹幕流（叠加层轮询消费）。"""
        self._stream.append(
            {
                "kind": kind,  # chat | system | welcome | gift | sing | next ...
                "text": text,
                "uname": uname,
                "uid": uid,
                "ts": time.time(),
            }
        )

    def recent(self, limit: int = 30) -> list[dict]:
        return list(self._stream)[-limit:]

    def clear(self) -> None:
        self._stream.clear()

    # ------------------------------------------------------------------ #
    def _dedup(self, key: str) -> bool:
        now = time.time()
        last = self._last_announce.get(key)
        if last is not None and now - last < _DEDUP_TTL_SEC:
            return True
        self._last_announce[key] = now
        # 清理过期 key（防止无限增长）
        if len(self._last_announce) > 200:
            self._last_announce = {
                k: v for k, v in self._last_announce.items() if now - v < _DEDUP_TTL_SEC * 2
            }
        return False


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #
_dispatcher: Optional[DanmakuDispatcher] = None


def get_dispatcher() -> DanmakuDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = DanmakuDispatcher()
    return _dispatcher
