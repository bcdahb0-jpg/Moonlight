"""主动陪聊策略（ProactivePolicy）。

替代纯空闲时间触发：综合画面显著变化、场景、置信度、用户状态、最近话题和冷却时间，
输出「值得提醒 / 适合轻聊 / 保持安静」三种结果（计划 §8 Phase 4）。

- coding + worth_interrupting → interrupt（报错/异常提醒）；
- chat / design / unknown + 高置信度 → light_chat（自然陪聊）；
- game + proactive_allow_game（默认 True）→ light_chat（桌宠陪伴：打游戏时陪聊）；
- video / reading / sensitive → silence（不打扰沉浸场景）；
- 冷却期内一律 silence（store.can_proactive 兜底）。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from .models import PolicyDecision, ScreenConfig, ScreenSnapshot
from .service import get_store

# 每个场景是否值得轻聊
_LIGHT_CHAT_SCENES = frozenset({"chat", "design", "unknown"})
# 沉浸场景：video/reading 绝不主动打扰；game 由 proactive_allow_game 决定。
_IMMERSIVE_SCENES = frozenset({"video", "reading"})


class ProactivePolicy:
    """无状态策略判定器（全部输入显式传入，便于单测）。"""

    def __init__(self, config: ScreenConfig) -> None:
        self.config = config

    def reload(self, config: ScreenConfig) -> None:
        self.config = config

    def decide(
        self,
        snapshot: Optional[ScreenSnapshot],
        cooldown_ok: bool = True,
        user_busy: bool = False,
        quiet_mode: bool = False,
    ) -> PolicyDecision:
        """综合决策。user_busy=AI 回复中/任务运行中；quiet_mode=勿扰时段。"""
        if not self.config.proactive_enabled:
            return PolicyDecision(kind="silence", reason="proactive_disabled")
        if quiet_mode:
            return PolicyDecision(kind="silence", reason="quiet_mode")
        if user_busy:
            return PolicyDecision(kind="silence", reason="user_busy")
        if not cooldown_ok:
            return PolicyDecision(kind="silence", reason="cooldown")
        if snapshot is None:
            return PolicyDecision(kind="silence", reason="no_snapshot")
        if snapshot.sensitive:
            return PolicyDecision(kind="silence", reason="sensitive")
        if snapshot.confidence < self.config.proactive_min_confidence:
            return PolicyDecision(
                kind="silence", reason="low_confidence"
            )

        scene = snapshot.scene
        if scene in _IMMERSIVE_SCENES:
            return PolicyDecision(kind="silence", reason=f"immersive_{scene}")

        if scene == "game":
            # 2026-08-11：桌宠陪伴场景 —— 打游戏时允许轻聊（默认开，可配置关）。
            if not self.config.proactive_allow_game:
                return PolicyDecision(kind="silence", reason="immersive_game")
            topic = snapshot.possible_topic or snapshot.summary or "游戏"
            return PolicyDecision(
                kind="light_chat",
                reason="scene_game",
                hint=f"我看到你在玩{topic}，加油！需要攻略或者想聊两句随时找我。",
            )

        if scene == "coding" and snapshot.worth_interrupting:
            topic = snapshot.possible_topic or "屏幕上的问题"
            return PolicyDecision(
                kind="interrupt",
                reason="coding_issue",
                hint=f"我看到你似乎在处理{topic}，如果需要帮忙看代码可以问我。",
            )

        if scene in _LIGHT_CHAT_SCENES or snapshot.worth_interrupting:
            topic = snapshot.possible_topic or snapshot.summary or "屏幕上的内容"
            return PolicyDecision(
                kind="light_chat",
                reason=f"scene_{scene}",
                hint=f"我看到你在看{topic}，想聊的话随时找我。",
            )

        return PolicyDecision(kind="silence", reason="no_match")


def decide_proactive_for(client_uid: str, *, user_busy: bool = False, quiet_mode: bool = False) -> PolicyDecision:
    """面向客户端的便捷入口（读取 store 状态 + 冷却 + 快照版本去重）。"""
    from .metrics import get_metrics

    store = get_store()
    if not store.can_proactive(client_uid):
        # 取真实抑制原因供日志/状态展示。
        get_metrics().inc_suppressed_reason("gate_denied")
        return PolicyDecision(kind="silence", reason="gate_denied")
    snap = store.latest_snapshot(client_uid)
    if snap is None:
        get_metrics().inc_suppressed_reason("no_snapshot")
        return PolicyDecision(kind="silence", reason="no_snapshot")
    # Phase 2（pet-ptt-workflow）：快照版本去重——上次主动对话之后没有新快照
    # （静态画面）不重复打扰；由定时巡检驱动时这层闸门拦截大部分无效触发。
    if not store.has_new_snapshot_since_last_proactive(client_uid):
        get_metrics().inc_suppressed_reason("no_new_snapshot")
        return PolicyDecision(kind="silence", reason="no_new_snapshot")
    policy = ProactivePolicy(store.config())
    decision = policy.decide(snap, cooldown_ok=True, user_busy=user_busy, quiet_mode=quiet_mode)
    if decision.kind != "silence":
        store.mark_proactive(client_uid)
        get_metrics().inc("proactive_count")
        logger.info(
            f"[screen_awareness] proactive={decision.kind} reason={decision.reason} "
            f"scene={snap.scene}"
        )
    else:
        get_metrics().inc_suppressed_reason(decision.reason or "unknown")
    return decision
