"""任务内核 middleware 链（plan §5.2）：ToolErrorHandling / LoopDetection / Clarification / Summary / ModelLengthFinishReason。

langchain 1.3 的 after_model 链**反向执行**：model → 链尾最先跑 → … → 链首最后跑 → model_to_tools 边。
故组装顺序取 [Clarification, ToolErrorHandling, LoopDetection, Summary, ModelLengthFinishReason]，
实际执行序为：**model → ModelLengthFinishReason（截断先拒）→ LoopDetection（防死循环）→ Clarification（澄清收尾）→ 边**。

- `ToolErrorHandling`：wrap_tool_call，工具异常 → error ToolMessage 回环（不中断 run）。
- `Summary`：before_model，每次进模型前按阈值压缩历史。
- 依赖：middleware.py ← graph.py；仅 import langchain + conf_bridge（单向依赖铁律）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    SummarizationMiddleware,
    ToolErrorMiddleware,
)
from langchain.agents.middleware.types import (
    AgentState,
    AgentMiddleware,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .conf_bridge import task_config

#: 六段式结构化摘要 prompt（v3 Phase 4，参考 pi compaction 结构化摘要）。
#: 无旧摘要时用 CREATE 模板；已有旧摘要时用 UPDATE 模板合并（pi `compaction.ts` 同思想）。
SUMMARY_PROMPT_CREATE = """\
你正在压缩一段任务智能体的对话历史。请提炼为结构化摘要，保留后续执行所需的关键信息。

只输出以下六个小节（每节 1-3 行，中文）：
【目标】任务目标与当前完成度
【约束】已知限制/规则/用户偏好
【进展】已完成的步骤与关键结果（含已读文件路径）
【关键决策】技术选型与方案取舍
【下一步】尚未完成的事项
【关键上下文】必须保留的事实（路径、命令、API、坑）

对话历史：
{messages}
"""

SUMMARY_PROMPT_UPDATE = """\
你正在压缩一段任务智能体的对话历史。已有旧摘要，请将新对话合并进旧摘要（保留原有信息，只增补变化）。

只输出以下六个小节（每节 1-3 行，中文）：
【目标】任务目标与当前完成度
【约束】已知限制/规则/用户偏好
【进展】已完成的步骤与关键结果（含已读文件路径）
【关键决策】技术选型与方案取舍
【下一步】尚未完成的事项
【关键上下文】必须保留的事实（路径、命令、API、坑）

旧摘要：
{existing_summary}

新对话：
{messages}
"""

#: 自动压缩触发后注入用户侧的新消息（与 compact_task_context 的 RemoveMessage 行为对齐）。
SUMMARY_SYSTEM_MESSAGE = "以下是之前的对话摘要（已压缩，历史消息已省略，可据此继续执行）："

#: ask_clarification 工具名（Clarification middleware 的 interrupt 目标，graph.py 注册同名工具）
CLARIFY_TOOL = "ask_clarification"


def _tool_call_signature(msg: AIMessage) -> str:
    """同一轮工具调用签名：(tool_name, args) 列表的排序序列化。"""
    return json.dumps(
        [(tc.get("name"), tc.get("args")) for tc in msg.tool_calls],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


class ToolErrorHandling(ToolErrorMiddleware):
    """捕获工具执行异常 → 以 `status="error"` 的 ToolMessage 回环，run 不中断。

    控制流信号（interrupt/父 Command）由内置逻辑放行，不误吞。
    """

    def __init__(self):
        super().__init__(on_error=self._on_error)

    @staticmethod
    def _on_error(exc: Exception, request: ToolCallRequest) -> str:
        name = request.tool.name if request.tool else request.tool_call["name"]
        # 只报类型 + 摘要，不泄漏内部/敏感细节（plan §5.2 错误处理靠前）
        return f"工具 `{name}` 执行失败（{type(exc).__name__}）：{exc}"


class LoopDetection(AgentMiddleware):
    """检测相同工具调用签名连续重复 `max_no_progress` 次 → `jump_to=end` 终止循环。

    无状态实现：直接扫描消息历史，统计当前模型输出**之前**连续重复的相同签名次数。
    - 跨超步累计：消息历史本身跨超步持久，计数不依赖会被每步清空的 EphemeralValue；
    - resume 自动重置：HITL interrupt 恢复后新模型输出的签名 ≠ 澄清签名，计数归零；
    - 用户新输入（HumanMessage）也会中断连续计数。
    """

    def __init__(self, max_no_progress: int | None = None):
        super().__init__()
        self.max_no_progress = max_no_progress or task_config().max_no_progress
        self.tools: list = []

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai is None or not last_ai.tool_calls:
            return None

        signature = _tool_call_signature(last_ai)
        count = 0
        seen_current = False
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                break  # 用户新输入 → 循环计数重置
            if not isinstance(m, AIMessage):
                continue  # ToolMessage/SystemMessage 跳过
            if not seen_current:
                seen_current = True  # 当前模型输出本身不计入
                continue
            if m.tool_calls and _tool_call_signature(m) == signature:
                count += 1
            else:
                break
        if count >= self.max_no_progress:
            return {"jump_to": "end"}  # 无进展 → 提前结束，防死循环
        return None


class Clarification(HumanInTheLoopMiddleware):
    """澄清收尾：`ask_clarification` 工具 → 原生 HITL interrupt（仅允许 respond）。

    必须排在 after_model 链**最后**执行（list 顺序靠前），确保 interrupt 是模型输出后的
    最终一步，且人工回复后回到 model 时不会再有其他 middleware 改写消息。
    """

    def __init__(self):
        super().__init__(
            interrupt_on={
                CLARIFY_TOOL: {"allowed_decisions": ["respond"]},
            }
        )


class Summary(SummarizationMiddleware):
    """上下文压缩：before_model 按阈值触发摘要（plan §5.2/§6a）。

    v3 Phase 4 增强（参考 pi compaction）：
    - 结构化六段式摘要 prompt（CREATE/UPDATE 合并）；
    - token 感知切点 `token_aware_cutoff`：按字符估算 token 找切点，绝不拆 toolResult
      （供 compact_task_context 手动压缩复用；自动触发路径 keep=("messages", N) 已保 AI/Tool 对）。

    默认 trigger=("tokens", 8000)、keep=("messages", 20)，可在组装时覆盖。
    """

    def __init__(
        self,
        model: Any,
        *,
        trigger: Any = ("tokens", 8000),
        keep: Any = ("messages", 20),
        summary_prompt: str | None = None,
    ):
        super().__init__(
            model=model,
            trigger=trigger,
            keep=keep,
            summary_prompt=summary_prompt or SUMMARY_PROMPT_CREATE,
        )

    @staticmethod
    def token_aware_cutoff(
        messages: list[Any],
        keep_recent_tokens: int,
        *,
        chars_per_token: int = 4,
    ) -> int:
        """按 token 估算找安全切点（pi `findCutPoint` 思想）。

        从尾部向前累加字符数（估 token），直到达到 `keep_recent_tokens`；
        切点**永不落在 ToolMessage 上**（向后调整越过 ToolMessage 行），
        保证 AI/Tool 对不拆散（deer-flow/pi 一致铁律）。

        Returns:
            切点 index（该 index 之前的消息被摘要化）；无消息可切时返回 0。
        """
        n = len(messages)
        if n == 0:
            return 0
        budget_chars = keep_recent_tokens * chars_per_token
        acc = 0
        cutoff = n
        for i in range(n - 1, -1, -1):
            m = messages[i]
            text = str(getattr(m, "content", "") or "")
            acc += len(text)
            if acc >= budget_chars:
                cutoff = i
                break
        # 向后越过连续的 ToolMessage（保 AI/Tool 对不拆散）
        while cutoff < n and _is_tool_message(messages[cutoff]):
            cutoff += 1
        # 预算足够覆盖全部历史 → 无需切（cutoff==n 且没 break 过）
        if acc < budget_chars:
            return 0
        return cutoff

    @staticmethod
    def structured_summary(
        summary: str,
        *,
        existing: str | None = None,
        messages: str = "",
    ) -> str:
        """六段式摘要文本（供 compact_task_context 复用；UPDATE 合并旧摘要）。"""
        if existing:
            return (
                SUMMARY_PROMPT_UPDATE.replace("{existing_summary}", existing)
                .replace("{messages}", messages)
            )
        return SUMMARY_PROMPT_CREATE.replace("{messages}", messages)


def _is_tool_message(m: Any) -> bool:
    return isinstance(m, ToolMessage)


class ModelLengthFinishReason(AgentMiddleware):
    """截断防护：LLM 返回 `finish_reason == "length"` → 整批拒绝执行工具（防残缺参数）。

    执行序最先（list 链尾），截断即 `jump_to=end`，工具永不执行、也永不进澄清。
    """

    def __init__(self):
        super().__init__()
        self.tools: list = []

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai is None or not self._is_length_truncated(last_ai):
            return None
        return {"jump_to": "end"}

    @staticmethod
    def _is_length_truncated(msg: AIMessage) -> bool:
        """OpenAI 兼容（含 DeepSeek）把 finish_reason 放 response_metadata；兼容 list 形式。"""
        meta = msg.response_metadata or {}
        fr = meta.get("finish_reason", meta.get("stop_reason"))
        if isinstance(fr, list):
            return any(x == "length" for x in fr)
        return fr == "length"


def build_middleware(model: Any) -> list[AgentMiddleware]:
    """按 plan §5.2 组装 5 个 middleware（顺序即执行语义，见模块 docstring）。"""
    return [
        Clarification(),
        ToolErrorHandling(),
        LoopDetection(),
        Summary(model),
        ModelLengthFinishReason(),
    ]
