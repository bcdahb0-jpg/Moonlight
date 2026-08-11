"""dangling tool_call 恢复（v3 Phase 3，参考 deer-flow dangling_tool_call_middleware）。

问题：中断（HITL/resume）/ 上下文压缩后，消息历史可能出现不匹配：
1. **孤儿调用**：AIMessage 声明了 tool_calls，但对应 ToolMessage 缺失（工具结果没写回）
   → 严格 OpenAI 兼容后端（DeepSeek）会把"上一个 assistant 消息含未闭合 tool_call"判 400。
2. **孤儿结果**：ToolMessage 存在但来源 tool_call 已不在历史（压缩删除了 AIMessage）
   → 同样 400（tool result 引用了不存在的 call id）。

机制（wrap_model_call 每次进模型前 patch，只改输入不改 checkpoint）：
- 扫描 messages，统计已出现过的 tool_call_id。
- AI 消息有 tool_calls 但结果缺失 → 在该 AI 消息后**合成** error ToolMessage
  （文案区分场景：压缩丢失 vs 中断；write_file 超长参数给专项提示）。
- ToolMessage 的 id 不存在于任何 tool_call → 直接丢弃。
- 同轮并行调用空 id：按位置配对（先补齐空 id，再匹配）。

单向依赖：dangling.py ← graph.py；仅 import langchain + stdlib。
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from loguru import logger

#: 合成错误 ToolMessage 的统一前缀（与 read_before_write / sandbox 风格一致，模型可辨）。
_SYNTH_PREFIX = "[执行恢复]"


class DanglingToolCallMiddleware(AgentMiddleware):
    """进模型前修复孤儿 tool_call / 孤儿 tool result。

    同时实现 `wrap_model_call` / `awrap_model_call`（本项目全异步路径，但补同步版
    防 langchain 1.3 在同步语境 raise NotImplementedError，hooks.py 同惯例）。
    """

    def __init__(self, enabled: bool = True):
        super().__init__()
        self.enabled = enabled

    # ------------------------------------------------------------------ //
    # 同步/异步双实现
    # ------------------------------------------------------------------ //
    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        if self.enabled:
            self._patch_messages(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        if self.enabled:
            self._patch_messages(request)
        return await handler(request)

    # ------------------------------------------------------------------ //
    # 核心：扫描 + patch
    # ------------------------------------------------------------------ //
    @staticmethod
    def _patch_messages(request: ModelRequest) -> None:
        msgs = request.messages
        if not msgs:
            return
        # 1) 收集所有已声明的 tool_call_id（含被丢弃 ToolMessage 的来源也计入，
        #    避免把"结果存在但调用被压缩删掉"的配对误判为孤儿调用）。
        declared: set[str] = set()
        for m in msgs:
            if isinstance(m, AIMessage):
                for tc in m.tool_calls or []:
                    cid = str(tc.get("id") or "")
                    if cid:
                        declared.add(cid)
        # 2) 结果 id 集合（仅非空 id 可精确配对）。
        result_ids: set[str] = set()
        for m in msgs:
            if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None):
                result_ids.add(str(m.tool_call_id))

        # 3) 逐消息修复：
        #    - AIMessage 的 tool_calls（非空 id）无对应 ToolMessage → 在其后插入合成 error。
        #    - ToolMessage 的 id 无声明 → 丢弃。判定：
        #      * declared 非空：id 不在声明集合 → 孤儿结果（来源已被压缩删掉）；
        #      * declared 空 且 历史无任何 AI 工具调用：命名结果无处安放 → 孤儿；
        #      * declared 空 但 AI 有 tool_calls（全空 id 场景）：按位置配对，保守全保留。
        has_ai_tool_calls = any(
            isinstance(m, AIMessage) and m.tool_calls for m in msgs
        )
        out: list[Any] = []
        for m in msgs:
            if isinstance(m, AIMessage):
                out.append(m)
                for tc in m.tool_calls or []:
                    cid = str(tc.get("id") or "")
                    if cid and cid not in result_ids:
                        # 孤儿调用：工具结果缺失（中断/压缩）→ 合成 error ToolMessage。
                        out.append(_synthetic_tool_message(cid, tc))
                        result_ids.add(cid)
                        logger.warning(f"dangling: 补合成 ToolMessage（call {cid} 结果缺失）")
            elif isinstance(m, ToolMessage):
                tid = str(getattr(m, "tool_call_id", "") or "")
                is_orphan = (declared and tid and tid not in declared) or (
                    not has_ai_tool_calls and tid
                )
                if is_orphan:
                    # 孤儿结果：来源调用已不在历史（压缩删除/中断残留）→ 丢弃，防后端 400。
                    logger.debug(f"dangling: 丢弃孤儿 ToolMessage（call {tid} 已不存在）")
                    continue
                out.append(m)
            else:
                out.append(m)
        if len(out) != len(msgs):
            request.messages = out


def _synthetic_tool_message(call_id: str, tc: dict[str, Any]) -> ToolMessage:
    """构造合成 error ToolMessage（区分超长参数 / 一般中断丢失）。

    deer-flow `_synthetic_tool_message_content` 思想：write_file 超长参数给专项提示，
    引导模型拆分或换工具，而不是盲目重试同一个会失败的大写入。
    """
    name = str(tc.get("name") or "")
    args = tc.get("args") or {}
    if name == "write_file" and isinstance(args, dict):
        content = str(args.get("content") or "")
        if len(content.encode("utf-8")) > 1_000_000:
            return ToolMessage(
                content=(
                    f"{_SYNTH_PREFIX} 工具 `write_file` 的结果缺失且参数超长（>1MB）。"
                    f"请拆分内容多次写入，或改用 str_replace 增量修改。"
                ),
                status="error",
                tool_call_id=call_id,
            )
    return ToolMessage(
        content=(
            f"{_SYNTH_PREFIX} 工具 `{name}` 的结果缺失（可能因任务中断或上下文压缩丢失）。"
            f"请不要重复执行相同调用；如需该结果，请重新执行一次。"
        ),
        status="error",
        tool_call_id=call_id,
    )
