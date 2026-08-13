"""游戏事件识别 + 高光喝彩（P4）。

- `classify_event(text, game)`：LLM 文本分类（graph.build_model）→
  {event_type, confidence, summary}；失败 fail-soft 返回 other。
- `EventBroker`：事件流（deque）+ 喝彩判定（HIGHLIGHT_EVENTS + 冷却）+
  喝彩话术模板。

参考：ZerolanLiveRobot（blip 画面理解 + 游戏实况对话）、AI-Desktop-Pet
（boredom 阈值主动发言——喝彩同思路：事件触发 + 冷却去重）。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Optional

from loguru import logger

from .game import HIGHLIGHT_EVENTS, get_game

# 喝彩话术模板（事件类型 → 模板列表）。
_CHEER_TEMPLATES: dict[str, list[str]] = {
    "kill": ["漂亮！{summary}！", "这波操作 6 啊：{summary}", "{summary}，帅！"],
    "victory": ["太强了！{summary}，全场最佳！", "拿下！{summary}，就这？", "{summary}——大神啊！"],
    "boss": ["BOSS 也扛不住你：{summary}！", "干得漂亮：{summary}"],
    "capture": ["收服成功！{summary}！", "{summary}，它归你啦！"],
    "levelup": ["升级了！{summary}，继续加油！", "{summary}，等级又高了！"],
    "research": ["研究完成：{summary}，科技树又亮了！", "{summary}，这波稳了！"],
}

_EVENT_TIMEOUT_SEC = 6.0  # LLM 事件分类超时


async def classify_event(text: str, game_id: str) -> dict:
    """LLM 把画面描述分类为游戏事件（fail-soft → other）。"""
    game = get_game(game_id)
    if not game:
        return {"event_type": "other", "confidence": 0.0, "summary": (text or "")[:32]}
    prompt = game["event_prompt"] + f"\n画面描述：{text[:300]}"
    try:
        from ..task_platform import conf_bridge, graph  # noqa: PLC0415
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        model = graph.build_model(conf_bridge.task_config())
        resp = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=prompt), HumanMessage(content="")]),
            timeout=_EVENT_TIMEOUT_SEC,
        )
        raw = str(getattr(resp, "content", "") or "").strip()
        parsed = _parse_json(raw)
        if not parsed:
            return {"event_type": "other", "confidence": 0.0, "summary": (text or "")[:32]}
        event_type = str(parsed.get("event_type") or "other").lower()
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "event_type": event_type,
            "confidence": round(confidence, 3),
            "summary": str(parsed.get("summary") or "")[:64],
        }
    except Exception as e:
        logger.debug(f"[playmate] classify_event failed: {type(e).__name__}")
        return {"event_type": "other", "confidence": 0.0, "summary": (text or "")[:32]}


def _parse_json(raw: str) -> Optional[dict]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    import json

    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class EventBroker:
    """游戏事件流 + 喝彩判定（全局单例）。"""

    def __init__(self, maxlen: int = 100) -> None:
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._last_cheer_at = 0.0
        self._cheer_cooldown = 60.0

    @property
    def recent_events(self) -> list[dict]:
        return list(self._events)

    def set_cooldown(self, seconds: float) -> None:
        self._cheer_cooldown = max(10.0, float(seconds))

    def record(self, event: dict) -> dict:
        """记录事件；高光事件返回喝彩文案（冷却内返回 None）。"""
        event = {**event, "ts": time.time()}
        self._events.append(event)
        cheer = None
        if event.get("event_type") in HIGHLIGHT_EVENTS:
            cheer = self._maybe_cheer(event)
        return {**event, "cheer": cheer}

    def _maybe_cheer(self, event: dict) -> Optional[str]:
        now = time.time()
        if now - self._last_cheer_at < self._cheer_cooldown:
            return None
        templates = _CHEER_TEMPLATES.get(event.get("event_type"), [])
        if not templates:
            return None
        self._last_cheer_at = now
        import random

        return random.choice(templates).format(summary=event.get("summary") or "干得漂亮")


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #
_broker: Optional[EventBroker] = None


def get_event_broker() -> EventBroker:
    global _broker
    if _broker is None:
        _broker = EventBroker()
    return _broker
