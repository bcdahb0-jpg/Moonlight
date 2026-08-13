"""轻量指标收集（无外部依赖，进程内累积）。

计划 §2 缺口 9 / §8 Phase 0：捕获、去重、上传、视觉分析、主动决策的耗时与命中率。
日志只记聚合数字，绝不记录标题全文 / OCR 文本（隐私铁律）。
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class _LatencyBucket:
    """滑动计数 + P50/P95 估算（固定桶近似，内存 O(1)）。"""

    def __init__(self, max_bins: int = 120) -> None:
        self._bins: list[tuple[float, float]] = []  # (timestamp, value_ms)
        self._max_bins = max_bins

    def add(self, value_ms: float) -> None:
        now = time.monotonic()
        self._bins.append((now, value_ms))
        if len(self._bins) > self._max_bins:
            # 只清最老一半，避免频繁分配。
            self._bins = self._bins[self._max_bins // 2 :]

    def _recent(self, window_sec: float = 3600.0) -> list[float]:
        now = time.monotonic()
        return [v for ts, v in self._bins if now - ts <= window_sec]

    def percentile(self, pct: float, window_sec: float = 3600.0) -> Optional[float]:
        vals = sorted(self._recent(window_sec))
        if not vals:
            return None
        idx = min(len(vals) - 1, int(len(vals) * pct))
        return round(vals[idx], 1)


class ScreenMetrics:
    """全局指标单例（进程内，多线程安全）。"""

    _instance: Optional["ScreenMetrics"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ScreenMetrics":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self.capture_latency = _LatencyBucket()
        self.analyze_latency = _LatencyBucket()
        self.frames_captured = 0
        self.frames_deduped = 0
        self.frames_dropped = 0  # 隐私/暂停/单飞丢弃
        self.analyze_count = 0
        self.analyze_errors = 0
        self.proactive_count = 0
        self.proactive_suppressed = 0
        self.vision_tokens = 0
        # Phase 5：输入/输出 token 分开计（成本估算用）。
        self.vision_input_tokens = 0
        self.vision_output_tokens = 0
        # Phase 5：识别反馈（有用/打扰/识别错误，本地策略调优用）。
        self.feedback_useful = 0
        self.feedback_disruptive = 0
        self.feedback_misrecognition = 0
        # Phase 6：主动发言抑制原因分布。
        self.proactive_suppressed_reasons: dict[str, int] = {}
        self._lock_internal = threading.Lock()

    # -- 计数 -- #
    def inc(self, name: str, n: int = 1) -> None:
        with self._lock_internal:
            if name == "frames_captured":
                self.frames_captured += n
            elif name == "frames_deduped":
                self.frames_deduped += n
            elif name == "frames_dropped":
                self.frames_dropped += n
            elif name == "analyze_count":
                self.analyze_count += n
            elif name == "analyze_errors":
                self.analyze_errors += n
            elif name == "proactive_count":
                self.proactive_count += n
            elif name == "proactive_suppressed":
                self.proactive_suppressed += n
            elif name == "vision_tokens":
                self.vision_tokens += n
            elif name == "vision_input_tokens":
                self.vision_input_tokens += n
            elif name == "vision_output_tokens":
                self.vision_output_tokens += n
            elif name == "feedback_useful":
                self.feedback_useful += n
            elif name == "feedback_disruptive":
                self.feedback_disruptive += n
            elif name == "feedback_misrecognition":
                self.feedback_misrecognition += n

    def inc_suppressed_reason(self, reason: str) -> None:
        with self._lock_internal:
            self.proactive_suppressed += 1
            self.proactive_suppressed_reasons[reason] = (
                self.proactive_suppressed_reasons.get(reason, 0) + 1
            )

    def observe(self, name: str, value_ms: float) -> None:
        if name == "capture":
            self.capture_latency.add(value_ms)
        elif name == "analyze":
            self.analyze_latency.add(value_ms)

    def snapshot(self) -> dict:
        with self._lock_internal:
            base = {
                "frames_captured": self.frames_captured,
                "frames_deduped": self.frames_deduped,
                "frames_dropped": self.frames_dropped,
                "analyze_count": self.analyze_count,
                "analyze_errors": self.analyze_errors,
                "proactive_count": self.proactive_count,
                "proactive_suppressed": self.proactive_suppressed,
                "proactive_suppressed_reasons": dict(
                    self.proactive_suppressed_reasons
                ),
                "vision_tokens": self.vision_tokens,
                "vision_input_tokens": self.vision_input_tokens,
                "vision_output_tokens": self.vision_output_tokens,
                "feedback_useful": self.feedback_useful,
                "feedback_disruptive": self.feedback_disruptive,
                "feedback_misrecognition": self.feedback_misrecognition,
            }
        base.update(
            {
                "capture_p50_ms": self.capture_latency.percentile(0.50),
                "capture_p95_ms": self.capture_latency.percentile(0.95),
                "analyze_p50_ms": self.analyze_latency.percentile(0.50),
                "analyze_p95_ms": self.analyze_latency.percentile(0.95),
                "dedupe_ratio": (
                    round(self.frames_deduped / max(1, self.frames_captured), 3)
                    if self.frames_captured
                    else 0.0
                ),
            }
        )
        return base


def get_metrics() -> ScreenMetrics:
    return ScreenMetrics()
