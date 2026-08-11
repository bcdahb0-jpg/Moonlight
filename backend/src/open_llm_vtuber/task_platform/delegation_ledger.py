"""委派账本（v4 Phase B2，参考 deer-flow delegation_ledger 思想精简版）。

问题：主 agent 多次委派子 agent 后，模型可能重复委派已完成的任务（记忆错位），
或不知道某个子任务已经在跑。同时子 agent 的完整结果文本会占上下文。

机制：
1. `extract_delegations(messages)`：从消息历史提取 `delegate` / `delegate_parallel`
   工具调用对（AIMessage.tool_calls ↔ ToolMessage），生成条目列表：
   {call_id, agent, description(任务前 200 字符), status(in_progress/done/error), result_brief(≤2000)}
2. `render_ledger(entries, max_entries)`：渲染为注入文本（新在前，超预算省略）。
3. `DelegationLedgerMiddleware`：wrap_model_call 每次进模型前，把账本作为
   `HumanMessage(name="delegation_ledger")` 插在消息最前（system 之后）。
   - 只 patch 输入、不进 checkpoint → 每次从 checkpoint 原始消息重新提取，不累积；
   - 与 dangling / compaction 兼容（只读历史，不删不改原消息）。
4. `bound_text(text, cap)`：结果/描述的统一 head/tail 截断（delegate 工具复用）。

单向依赖：delegation_ledger.py ← graph.py / agents/tools.py；仅 import langchain + stdlib。
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from loguru import logger

#: 委派任务描述截断上限（入账本）。
_DESCRIPTION_CAP = 200
#: 委派结果入账本的长度上限（head/tail 各半）。
_DEFAULT_RESULT_CAP = 2000
#: 账本渲染预算。
_LEDGER_RENDER_BUDGET = 6000
#: 账本条目名（langchain 消息 name 字段，日志可辨）。
_LEDGER_MSG_NAME = "delegation_ledger"
#: 被跟踪的委派工具名。
_DELEGATE_TOOLS = ("delegate", "delegate_parallel")


def bound_text(text: str, cap: int = _DEFAULT_RESULT_CAP) -> str:
    """确定性 head/tail 截断（非 LLM 摘要，deer-flow `_bound_text` 同思想）。"""
    if len(text) <= cap:
        return text
    if cap <= 0:
        return ""
    head = cap * 2 // 3
    marker = "\n..."
    if cap <= len(marker):
        return text[:cap]
    tail = cap - head - len(marker)
    if tail <= 0:
        return text[:cap]
    return f"{text[:head]}{marker}{text[-tail:]}"


def _tc_name(tc: dict[str, Any]) -> str:
    return str(tc.get("name") or "")


def _tc_args(tc: dict[str, Any]) -> dict[str, Any]:
    args = tc.get("args")
    return args if isinstance(args, dict) else {}


def _tc_id(tc: dict[str, Any]) -> str:
    return str(tc.get("id") or "")


def _msg_text(content: Any) -> str:
    return str(content) if content is not None else ""


def extract_delegations(
    messages: list[Any],
    *,
    result_cap: int = _DEFAULT_RESULT_CAP,
) -> list[dict[str, Any]]:
    """从消息历史提取委派条目（按出现顺序，配对 AI 调用与结果）。"""
    entries: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                if _tc_name(tc) not in _DELEGATE_TOOLS:
                    continue
                cid = _tc_id(tc)
                args = _tc_args(tc)
                description = str(
                    args.get("task")
                    or args.get("delegations")
                    or args.get("description")
                    or ""
                )[:_DESCRIPTION_CAP]
                entry: dict[str, Any] = {
                    "call_id": cid,
                    "agent": str(args.get("agent_name") or args.get("agent") or "?"),
                    "description": description,
                    "status": "in_progress",
                    "result_brief": "",
                }
                if cid not in by_id:
                    order.append(cid)
                by_id[cid] = entry
        elif isinstance(msg, ToolMessage):
            cid = str(getattr(msg, "tool_call_id", "") or "")
            entry = by_id.get(cid)
            if entry is None:
                continue
            entry["status"] = "done" if getattr(msg, "status", None) != "error" else "error"
            entry["result_brief"] = bound_text(_msg_text(msg.content), result_cap)
    return [by_id[cid] for cid in order if cid in by_id]


def render_ledger(
    entries: list[dict[str, Any]],
    *,
    max_entries: int = 8,
    render_budget: int = _LEDGER_RENDER_BUDGET,
) -> str:
    """渲染委派账本为模型可见文本（新在前；超预算省略旧条目）。"""
    if not entries:
        return ""
    lines = [
        "## 已委派工作（状态指引：in_progress 勿重复委派；done 可直接复用结果；error 可换方案重试）",
    ]
    rendered = 0
    for entry in reversed(entries):  # 新的在前
        if rendered >= max_entries:
            lines.append(f"- ... 另有 {len(entries) - rendered} 条更早委派（超出预算未列出）")
            break
        status = entry.get("status", "?")
        agent = entry.get("agent") or "?"
        desc = entry.get("description") or "(无描述)"
        line = f"- [{status}] agent={agent} 任务=\"{desc}\""
        brief = entry.get("result_brief") or ""
        if brief:
            line += f" → 结果: {brief[:120]}{'…' if len(brief) > 120 else ''}"
        if len("\n".join(lines) + "\n" + line) > render_budget:
            lines.append(f"- ... 其余委派条目超出上下文预算未列出")
            break
        lines.append(line)
        rendered += 1
    return "\n".join(lines)


class DelegationLedgerMiddleware(AgentMiddleware):
    """委派账本注入：wrap_model_call 进模型前动态提取 + 前置注入。

    只 patch 模型输入（每次从 checkpoint 原始消息重新提取），不改 checkpoint；
    账本消息 name=delegation_ledger 便于日志排查；不参与工具调用（无 id）。
    """

    def __init__(self, *, enabled: bool = True, max_entries: int = 8, result_cap: int = _DEFAULT_RESULT_CAP):
        super().__init__()
        self.enabled = enabled
        self.max_entries = max_entries
        self.result_cap = result_cap

    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        if self.enabled:
            self._inject(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        if self.enabled:
            self._inject(request)
        return await handler(request)

    def _inject(self, request: ModelRequest) -> None:
        msgs = request.messages
        if not msgs:
            return
        entries = extract_delegations(msgs, result_cap=self.result_cap)
        if not entries:
            return
        ledger = render_ledger(entries, max_entries=self.max_entries)
        if not ledger:
            return
        request.messages = [HumanMessage(content=ledger, name=_LEDGER_MSG_NAME), *msgs]
        logger.debug(f"delegation_ledger: 注入 {len(entries)} 条委派账本")


__all__: list[str] = ["DelegationLedgerMiddleware", "extract_delegations", "render_ledger", "bound_text"]
