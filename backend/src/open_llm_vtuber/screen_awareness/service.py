"""会话级短时上下文缓存（TTL + 窗口身份失效 + 可取消 latest-wins）。

- 每个 client_uid 独立维护最近有效帧 / 视觉摘要；
- 摘要 TTL 过期或窗口切换（title/app/pid 身份变化）立即失效；
- 同一客户端最多 1 个视觉分析任务在飞：in-flight 期间只保留最新帧，
  当前任务结束后继续分析，避免慢模型期间一直分析旧窗口；
- clear/disable 会递增 generation。已经在远端执行的任务即使返回，也不得
  把旧摘要写回已清空的客户端上下文；
- 原始图像仅保留最近一帧且 ≤ 30s，分析完成/过期立即释放（不落盘）。
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Optional

from loguru import logger

from .analyzer import VisionAnalyzer
from .metrics import get_metrics
from .models import (
    ScreenConfig,
    ScreenFrame,
    ScreenSnapshot,
    ScreenStatus,
    ScreenWindowInfo,
)
from .privacy import PrivacyPolicy

_IMAGE_RETENTION_SEC = 30.0
_IMAGE_MAX_BYTES = 4 * 1024 * 1024  # 4MB 上限，防超大帧拖垮内存


class ScreenContext:
    """单个客户端的屏幕上下文（线程安全）。"""

    __slots__ = (
        "_lock",
        "enabled",
        "snapshot",
        "snapshot_at",
        "window",
        "frame",
        "frame_at",
        "analyzing",
        "in_flight",
        "pending_frame",
        "generation",
        "last_proactive_at",
        "pause_reason",
        # Phase 2（pet-ptt-workflow）：快照版本号 + 上次主动对话消费的版本号。
        # 静态画面不重复打扰：decide_proactive_for 只允许版本晚于
        # last_proactive_snapshot_id 的快照通过 ProactivePolicy。
        "snapshot_seq",
        "last_proactive_snapshot_id",
    )

    def __init__(self) -> None:
        # ingest() may return a status while holding the context lock (for
        # deduplication/queue decisions).  RLock keeps that synchronous read
        # safe without blocking the event loop on a self-reacquire.
        self._lock = threading.RLock()
        self.enabled = False
        self.snapshot: Optional[ScreenSnapshot] = None
        self.snapshot_at: Optional[float] = None
        self.window: Optional[ScreenWindowInfo] = None
        self.frame: Optional[ScreenFrame] = None
        self.frame_at: Optional[float] = None
        self.analyzing = False
        self.in_flight = False
        self.pending_frame: Optional[ScreenFrame] = None
        # 逻辑取消令牌：不依赖底层 provider 是否支持真正取消。
        self.generation = 0
        self.last_proactive_at: Optional[float] = None
        self.pause_reason = ""
        self.snapshot_seq = 0
        self.last_proactive_snapshot_id: Optional[int] = None


class ScreenContextStore:
    """全局 store（进程内单例）。"""

    _instance: Optional["ScreenContextStore"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ScreenContextStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._contexts: dict[str, ScreenContext] = {}
        self._ctx_lock = threading.Lock()
        self._config: ScreenConfig = ScreenConfig()
        self.analyzer = VisionAnalyzer(self._config)
        self._privacy = PrivacyPolicy(self._config)
        self._enabled = False

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def configure(self, config: ScreenConfig, enabled: bool | None = None) -> None:
        self._config = config
        self.analyzer.reload(config)
        self._privacy.reload(config)
        if enabled is not None:
            self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled and self._config.enabled

    def config(self) -> ScreenConfig:
        return self._config

    def _ctx(self, client_uid: str) -> ScreenContext:
        with self._ctx_lock:
            ctx = self._contexts.get(client_uid)
            if ctx is None:
                ctx = ScreenContext()
                self._contexts[client_uid] = ctx
            return ctx

    # ------------------------------------------------------------------ #
    # 采集入口（供 WS screen-frame 处理器 / 测试调用）
    # ------------------------------------------------------------------ #
    def set_client_enabled(self, client_uid: str, enabled: bool, pause_reason: str = "") -> None:
        ctx = self._ctx(client_uid)
        with ctx._lock:
            if ctx.enabled and not enabled:
                ctx.generation += 1
                ctx.pending_frame = None
            ctx.enabled = enabled
            ctx.pause_reason = "" if enabled else pause_reason

    def pause(self, client_uid: str, reason: str) -> None:
        self.set_client_enabled(client_uid, False, reason)

    async def ingest(
        self, client_uid: str, frame: ScreenFrame, api_key: str = ""
    ) -> ScreenStatus:
        """接收一帧：隐私校验 → 去重 → latest-wins 分析。

        只有当前 generation 的分析结果才能提交到 context。这样 clear/disable
        不必等待远端 HTTP 超时，也能保证旧结果不会复活。
        """
        ctx = self._ctx(client_uid)
        if not self.is_enabled() or not ctx.enabled:
            return self.status(client_uid)

        # ① 隐私二次校验（后端兜底）。
        blocked, reason = self._privacy.check(frame.window)
        if blocked:
            get_metrics().inc("frames_dropped")
            ctx.pause_reason = reason
            logger.info(f"[screen_awareness] frame blocked: {reason}")
            return self.status(client_uid)

        next_frame: Optional[ScreenFrame] = frame
        while next_frame is not None:
            now = time.monotonic()
            early = False
            job_generation = 0
            with ctx._lock:
                if not ctx.enabled or not self.is_enabled():
                    return self.status(client_uid)
                prev_win = ctx.window
                same_window = (
                    prev_win is not None
                    and prev_win.title == next_frame.window.title
                    and prev_win.app == next_frame.window.app
                    and prev_win.pid == next_frame.window.pid
                )
                same_image = bool(
                    prev_win is not None
                    and same_window
                    and ctx.frame is not None
                    and ctx.frame.image_hash
                    and ctx.frame.image_hash == next_frame.image_hash
                )
                if same_image:
                    get_metrics().inc("frames_deduped")
                    early = True
                elif ctx.in_flight:
                    # latest-wins：保留最新帧，由当前分析任务在 finally 后继续处理。
                    ctx.pending_frame = next_frame
                    get_metrics().inc("frames_dropped")
                    return self.status(client_uid)
                else:
                    get_metrics().inc("frames_captured")
                    ctx.window = next_frame.window
                    ctx.frame = next_frame
                    ctx.frame_at = now
                    ctx.in_flight = True
                    ctx.pause_reason = ""
                    job_generation = ctx.generation
            if early:
                return self.status(client_uid)

            snap = None
            try:
                snap = await self.analyzer.analyze(next_frame, api_key=api_key)
            finally:
                with ctx._lock:
                    current = (
                        ctx.generation == job_generation
                        and ctx.enabled
                        and self.is_enabled()
                    )
                    if current and snap is not None:
                        ctx.snapshot = snap
                        ctx.snapshot_at = now
                        # 新快照版本号递增（静态画面去重依据）。
                        ctx.snapshot_seq += 1
                    ctx.in_flight = False
                    # disable/clear 会主动清掉 pending；只有同一代任务才继续消费。
                    next_frame = ctx.pending_frame if current else None
                    ctx.pending_frame = None
            if next_frame is None:
                return self.status(client_uid)
        return self.status(client_uid)

    # ------------------------------------------------------------------ #
    # 读取（聊天上下文融合用）
    # ------------------------------------------------------------------ #
    def latest_snapshot(self, client_uid: str, now: Optional[float] = None) -> Optional[ScreenSnapshot]:
        """返回未过期且窗口未变的摘要；过期/无窗口返回 None。"""
        ctx = self._ctx(client_uid)
        with ctx._lock:
            return self._latest_snapshot_locked(ctx, now)

    def _latest_snapshot_locked(
        self, ctx: ScreenContext, now: Optional[float] = None
    ) -> Optional[ScreenSnapshot]:
        if not ctx.enabled or ctx.snapshot is None or ctx.snapshot_at is None:
            return None
        n = now or time.monotonic()
        if n - ctx.snapshot_at > self._config.summary_ttl_sec:
            return None
        if ctx.window is None:
            return None
        return ctx.snapshot

    def latest_frame(self, client_uid: str, max_age_sec: float = _IMAGE_RETENTION_SEC) -> Optional[ScreenFrame]:
        """最近有效帧（含图像）。仅用户明确要看屏幕时使用；过期即 None。"""
        ctx = self._ctx(client_uid)
        with ctx._lock:
            if ctx.frame is None or ctx.frame_at is None or not ctx.enabled:
                return None
            if time.monotonic() - ctx.frame_at > max_age_sec:
                return None
            if not ctx.frame.image:
                return None
            if len(ctx.frame.image) > _IMAGE_MAX_BYTES:
                return None
            return ctx.frame

    def context_bundle(self, client_uid: str) -> dict[str, Any]:
        """给对话注入的临时 turn context（摘要 + 可选图像）。"""
        snap = self.latest_snapshot(client_uid)
        out: dict[str, Any] = {
            "snapshot": snap.model_dump(mode="json") if snap else None,
            "frame": None,
        }
        if snap is not None:
            frm = self.latest_frame(client_uid)
            if frm is not None and frm.image:
                out["frame"] = {
                    "frame_id": frm.frame_id,
                    "captured_at": frm.captured_at,
                    "image": frm.image,
                    "window": frm.window.model_dump(mode="json"),
                }
        return out

    # ------------------------------------------------------------------ #
    # 主动陪聊
    # ------------------------------------------------------------------ #
    def can_proactive(
        self, client_uid: str, min_confidence: float | None = None
    ) -> bool:
        """是否允许主动陪聊（冷却 + 摘要有效性 + worth_interrupting/置信度）。"""
        ctx = self._ctx(client_uid)
        conf = min_confidence if min_confidence is not None else self._config.proactive_min_confidence
        with ctx._lock:
            if not self.is_enabled() or not ctx.enabled:
                return False
            if not self._config.proactive_enabled:
                return False
            n = time.monotonic()
            if (
                ctx.last_proactive_at is not None
                and n - ctx.last_proactive_at < self._config.proactive_cooldown_sec
            ):
                return False
            snap = self._latest_snapshot_locked(ctx, n)
            if snap is None:
                return False
            if snap.confidence < conf:
                return False
            return True

    def mark_proactive(self, client_uid: str) -> None:
        ctx = self._ctx(client_uid)
        with ctx._lock:
            ctx.last_proactive_at = time.monotonic()
            # Phase 2（pet-ptt-workflow）：记录本次主动对话消费的快照版本，
            # 之后只有版本更新的快照才允许再次主动（静态画面去重）。
            ctx.last_proactive_snapshot_id = ctx.snapshot_seq

    def has_new_snapshot_since_last_proactive(self, client_uid: str) -> bool:
        """是否有「上次主动对话之后」产生的新快照（静态画面去重闸门）。

        未启用 / 无摘要 → False；从未主动过（last_proactive_snapshot_id=None）→
        当前快照视为新；否则要求 snapshot_seq 严格大于上次消费的版本。
        """
        ctx = self._ctx(client_uid)
        with ctx._lock:
            if not self.is_enabled() or not ctx.enabled:
                return False
            if ctx.snapshot is None:
                return False
            if (
                ctx.last_proactive_snapshot_id is not None
                and ctx.snapshot_seq <= ctx.last_proactive_snapshot_id
            ):
                return False
            return True

    # ------------------------------------------------------------------ #
    # 状态 / 清理
    # ------------------------------------------------------------------ #
    def status(self, client_uid: str) -> ScreenStatus:
        ctx = self._ctx(client_uid)
        metrics = get_metrics()
        with ctx._lock:
            snap = ctx.snapshot
            win = ctx.window
            return ScreenStatus(
                enabled=self.is_enabled() and ctx.enabled,
                capturing=ctx.in_flight,
                last_capture_at=ctx.frame_at,
                last_analyze_at=ctx.snapshot_at,
                last_window_title=win.title if win else "",
                last_window_app=win.app if win else "",
                last_scene=snap.scene if snap else "",
                last_summary=snap.summary if snap else "",
                pause_reason=ctx.pause_reason,
                pending_frames=1 if ctx.pending_frame is not None else 0,
                frames_captured=metrics.frames_captured,
                frames_deduped=metrics.frames_deduped,
                analyze_count=metrics.analyze_count,
                last_error=(
                    f"analyze_errors={metrics.analyze_errors}"
                    if metrics.analyze_errors
                    else ""
                ),
            )

    def clear(self, client_uid: str) -> None:
        """清除该客户端的内存上下文（图像 + 摘要 + 窗口身份）。"""
        ctx = self._ctx(client_uid)
        with ctx._lock:
            ctx.generation += 1
            ctx.pending_frame = None
            ctx.snapshot = None
            ctx.snapshot_at = None
            ctx.window = None
            ctx.frame = None
            ctx.frame_at = None
            ctx.pause_reason = ""

    def clear_all(self) -> None:
        with self._ctx_lock:
            for ctx in self._contexts.values():
                self.clear_ctx_locked(ctx)
        # 清空映射释放引用
        with self._ctx_lock:
            self._contexts.clear()

    def clear_ctx_locked(self, ctx: ScreenContext) -> None:
        with ctx._lock:
            ctx.generation += 1
            ctx.pending_frame = None
            ctx.snapshot = None
            ctx.snapshot_at = None
            ctx.window = None
            ctx.frame = None
            ctx.frame_at = None
            ctx.pause_reason = ""

    def sweep(self) -> int:
        """过期清理：删除超过 2 分钟无活动的客户端上下文，返回清理数。"""
        now = time.monotonic()
        expired = []
        with self._ctx_lock:
            for uid, ctx in self._contexts.items():
                with ctx._lock:
                    last_active = max(
                        [t for t in (ctx.frame_at, ctx.snapshot_at) if t is not None] or [0]
                    )
                if now - last_active > 120.0:
                    expired.append(uid)
            for uid in expired:
                self._contexts.pop(uid, None)
        return len(expired)


def get_store() -> ScreenContextStore:
    return ScreenContextStore()
