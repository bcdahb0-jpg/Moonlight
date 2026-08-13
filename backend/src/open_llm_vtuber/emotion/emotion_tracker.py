"""情绪状态跟踪器：维护当前情绪、情绪变化事件队列，可选持久化。

数据文件：`<backend>/emotion_state.json`（原子写），schema:
  {
    "emotion": "joy",              // 当前情绪
    "confidence": 0.8,             // 置信度 0..1
    "updated_at": "2026-08-04T12:00:00",  // ISO 时间戳
    "history": [{"emotion": "joy", "count": 3}]  // 情绪计数直方图
  }
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from loguru import logger

from .emotion_analyzer import EMOTIONS


class EmotionTracker:
    """线程安全的情绪跟踪器（后端为异步环境，但 MCP 线程可能并发访问）。

    P1（emotion-machine 卡片）：支持**会话级情绪**——`update/get_current`
    传 `uid` 时按会话独立维护，未传则保持全局；会话情绪带衰减
    （`decay_minutes` 内无更新自动回归 neutral）。
    """

    def __init__(
        self,
        state_path: Optional[str] = None,
        history_size: int = 20,
        decay_minutes: float = 30.0,
    ) -> None:
        self._state_path = state_path
        self._current: str = "neutral"
        self._confidence: float = 0.5
        self._events: Deque[dict] = deque(maxlen=50)
        self._histogram: Dict[str, int] = {e: 0 for e in EMOTIONS}
        self._llm_cache: Dict[str, dict] = {}
        # P1：会话级情绪（uid → {emotion, confidence, updated_at}），内存态。
        self._conversations: Dict[str, dict] = {}
        self._decay_minutes: float = max(1.0, float(decay_minutes))
        self._lock = threading.Lock()
        self._load()

    # ---- 公共 API ----
    def update(
        self, emotion: str, confidence: float = 0.5, source: str = "analyzer", uid: Optional[str] = None
    ) -> dict:
        """更新情绪，返回事件对象（供 WS 推送）。uid 非空时写入会话级。"""
        emotion = emotion if emotion in EMOTIONS else "neutral"
        with self._lock:
            if uid:
                self._conversations[uid] = {
                    "emotion": emotion,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "updated_at": time.time(),
                }
                event = {
                    "emotion": emotion,
                    "confidence": self._conversations[uid]["confidence"],
                    "source": source,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "conversation": uid,
                }
                self._events.append(event)
                return event
            self._current = emotion
            self._confidence = max(0.0, min(1.0, confidence))
            self._histogram[emotion] = self._histogram.get(emotion, 0) + 1
            event = {
                "emotion": emotion,
                "confidence": self._confidence,
                "source": source,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            self._events.append(event)
        self._save()
        return event

    def set(self, emotion: str, confidence: float = 1.0) -> dict:
        """手动覆盖情绪（供 REST/插件调用）。"""
        return self.update(emotion, confidence, source="manual")

    def reset(self) -> dict:
        """重置为 neutral。"""
        return self.update("neutral", 1.0, source="reset")

    def get_current(self, uid: Optional[str] = None) -> dict:
        with self._lock:
            if uid:
                entry = self._conversations.get(uid)
                if entry is None:
                    return {"emotion": "neutral", "confidence": 0.5, "histogram": {}, "conversation": uid}
                # 衰减：超过 decay_minutes 未更新 → 回归 neutral
                if time.time() - entry["updated_at"] > self._decay_minutes * 60:
                    entry = {"emotion": "neutral", "confidence": 0.4, "updated_at": time.time()}
                    self._conversations[uid] = entry
                return {
                    "emotion": entry["emotion"],
                    "confidence": entry["confidence"],
                    "histogram": dict(self._histogram),
                    "conversation": uid,
                }
            return {
                "emotion": self._current,
                "confidence": self._confidence,
                "histogram": dict(self._histogram),
            }

    def state_machine(self, uid: Optional[str] = None) -> dict:
        """状态机视图（emotion-machine 卡片数据源）。"""
        current = self.get_current(uid)
        return {
            "states": list(EMOTIONS),
            "current": current.get("emotion", "neutral"),
            "confidence": current.get("confidence", 0.5),
            "decay_minutes": self._decay_minutes,
            "per_conversation": bool(uid),
        }

    def recent_events(self, limit: int = 10) -> List[dict]:
        with self._lock:
            return list(self._events)[-limit:]

    # ---- LLM 慢路径缓存（Phase 1：句子流预取 → 音频 payload 消费） ----

    _LLM_CACHE_TTL_SEC = 60.0

    def set_llm_cache(self, fingerprint: str, emotion: str, intensity: float, duration_ms: int) -> None:
        """写入 LLM 情绪分类缓存（带文本指纹与 TTL）。"""
        if emotion not in EMOTIONS:
            return
        with self._lock:
            self._llm_cache[fingerprint] = {
                "emotion": emotion,
                "intensity": max(0.0, min(1.0, float(intensity))),
                "duration_ms": int(duration_ms),
                "ts": time.time(),
            }

    def get_llm_cache(self, fingerprint: str) -> Optional[dict]:
        """读取 LLM 情绪缓存；过期或不存在返回 None。"""
        with self._lock:
            entry = self._llm_cache.get(fingerprint)
            if not entry:
                return None
            if time.time() - entry.get("ts", 0) > self._LLM_CACHE_TTL_SEC:
                self._llm_cache.pop(fingerprint, None)
                return None
            return {"emotion": entry["emotion"], "intensity": entry["intensity"], "duration_ms": entry["duration_ms"]}

    # ---- 持久化 ----
    def _load(self) -> None:
        if not self._state_path or not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._current = data.get("emotion", "neutral")
            self._confidence = data.get("confidence", 0.5)
            hist = data.get("history", {})
            for k, v in hist.items():
                if k in self._histogram:
                    self._histogram[k] = int(v)
        except Exception as e:
            logger.warning(f"Failed to load emotion state: {e}")

    def _save(self) -> None:
        if not self._state_path:
            return
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            payload = {
                "emotion": self._current,
                "confidence": self._confidence,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "history": self._histogram,
            }
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_path)
        except Exception as e:
            logger.debug(f"Failed to persist emotion state: {e}")
