"""token 预算前瞻（v3 Phase 6，参考 deer-flow token_budget_middleware）。

长任务超上下文窗口前预警/硬停：
- **软警告**（ratio > warn_ratio，默认 0.8）：本轮回合已结束 → 下一轮进模型前作为
  HumanMessage 注入提示（deer-flow `_pending_warnings` 的 deferred 思想）。
- **硬停**（ratio > hard_ratio，默认 0.95）：剥离开本轮 tool_calls、`jump_to=end`
  （deer-flow `_build_hard_stop_update` 思想），返回文本提示 /compact 后续跑。

token 估算：优先用消息 `usage_metadata`（langchain 自带，OpenAI 兼容返回
response_metadata.usage 时自动填充），缺失时按字符/4 粗估（pi 同法）。
上下文窗口来源：`llm_adapter.context_window(cfg)`（DeepSeek 1M / 显式配置 / 回退 64k）。

与 `ModelLengthFinishReason`（finish_reason=length 拒批）互补：
- 本模块：历史**累计**超预算（进模型前可预见）；
- ModelLengthFinishReason：**本次生成**被截断（模型输出后才发现）。

单向依赖：token_budget.py ← graph.py；仅 import langchain + conf_bridge/llm_adapter。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage
from loguru import logger

from . import llm_adapter
from .conf_bridge import TaskPlatformConfig, task_config


def estimate_tokens(messages: list[Any]) -> int:
    """估算消息历史 token 数：优先取**最近一次**模型调用的真实 usage，缺失按字符/4 粗估。

    v6.6 修复（假 HARD STOP 根因）：`usage_metadata.total_tokens` 是**单次 API 调用**的
    输入+输出总量——每次调用输入都包含全部历史，若把每条消息的 usage 累加会重复计数
    （实测虚高 ~9.6 倍：真实 6.8K 被计成 65K，12 轮工具调用即触发 64K 假硬停）。
    正确语义：最后一次调用的 total_tokens 即当前上下文真实占用，取最后一条带 usage 的消息。
    """
    for m in reversed(messages):
        um = getattr(m, "usage_metadata", None)
        if isinstance(um, dict) and um.get("total_tokens"):
            return int(um["total_tokens"])
    total = 0
    for m in messages:
        text = str(getattr(m, "content", "") or "")
        total += len(text) // 4
    return total


def build_budget_message(used: int, window: int) -> str:
    return (
        f"[系统] 上下文预算警告：已使用约 {used:,} / {window:,} tokens"
        f"（{used * 100 // window}%）。为防超限，建议执行 /compact 压缩历史，"
        f"或拆分当前任务。后续回复请尽量精简。"
    )


class TokenBudgetMiddleware(AgentMiddleware):
    """上下文预算前瞻：after_model 累计 token，超软阈值 → 下轮注入警告；超硬阈值 → 硬停。

    与 LoopDetection/ModelLengthFinishReason 同为 after_model 链成员（执行序由
    build_middleware 组装顺序决定，本模块插在 Summary 之后）。
    """

    def __init__(
        self,
        cfg: TaskPlatformConfig | None = None,
        *,
        warn_ratio: float | None = None,
        hard_ratio: float | None = None,
    ):
        super().__init__()
        cfg = cfg or task_config()
        self.window = llm_adapter.context_window(cfg)
        self.warn_ratio = warn_ratio or cfg.token_budget_warn_ratio
        self.hard_ratio = hard_ratio or cfg.token_budget_hard_ratio
        self._pending_warning: str | None = None

    def _used(self, state: AgentState[Any]) -> int:
        return estimate_tokens(state.get("messages") or [])

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        used = self._used(state)
        ratio = used / self.window if self.window else 0.0

        # 硬停：本轮已超预算 → 剥离开 tool_calls（防残缺参数执行），jump_to=end。
        if ratio >= self.hard_ratio:
            logger.warning(f"token_budget: HARD STOP {used}/{self.window} ({ratio:.0%})")
            messages = state.get("messages") or []
            last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
            if last_ai is not None and last_ai.tool_calls:
                last_ai.tool_calls = []  # 剥离工具调用（DeferredToolMessage 兼容）
            return {
                "jump_to": "end",
                "messages": [
                    HumanMessage(
                        content=(
                            f"[系统] 上下文已满（{used:,}/{self.window:,} tokens）。"
                            f"任务已暂停：请执行 /compact 压缩历史后继续。"
                        )
                    )
                ],
            }

        # 软警告：本轮回合结束 → 下一轮 before_model 注入（deferred）。
        if ratio >= self.warn_ratio and self._pending_warning is None:
            self._pending_warning = build_budget_message(used, self.window)
            logger.info(f"token_budget: WARN {used}/{self.window} ({ratio:.0%})")
        return None

    def before_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        if self._pending_warning is not None:
            warning = self._pending_warning
            self._pending_warning = None
            return {"messages": [HumanMessage(content=warning)]}
        return None
