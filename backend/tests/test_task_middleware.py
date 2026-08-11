"""Phase 2a：5 个 middleware 测试（TDD，plan §5.2）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_middleware.py -q --basetemp=./.pytest-tmp

覆盖：
- ToolErrorHandling：工具抛异常 → error ToolMessage 回环，run 不中断。
- LoopDetection：相同工具签名连续 max_no_progress 次 → jump_to=end 终止。
- Clarification：ask_clarification → HITL interrupt（全链内验证）。
- Summary：before_model 阈值触发压缩。
- ModelLengthFinishReason：finish_reason=length → 整批拒绝执行工具。
"""
import asyncio
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import middleware as mw
from src.open_llm_vtuber.task_platform.state import TaskState


# --------------------------------------------------------------------------- #
# 脚手架
# --------------------------------------------------------------------------- #
class _StubModel(BaseChatModel, BaseModel):
    responses: list[BaseMessage] = Field(default_factory=list)
    i: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        n = self.i
        self.i += 1
        msg = self.responses[n % len(self.responses)] if self.responses else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _stub_model(*responses: BaseMessage) -> _StubModel:
    return _StubModel(responses=list(responses))


def _tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "id": call_id, "args": args}


@tool
def ask_clarification(question: str, options: Optional[list[str]] = None) -> str:
    """需要用户澄清时调用。真实返回由 HITL respond 决策注入，工具本身不执行。"""
    return ""


def _make_rec_add():
    """带调用记录的加法工具（验证工具是否真实执行）。"""
    calls: list = []

    @tool
    def rec_add(a: int, b: int) -> int:
        """加法（记录调用）。"""
        calls.append((a, b))
        return a + b

    return rec_add, calls


@tool
def boom(x: str = "") -> str:
    """故意抛异常，验证工具错误回环。"""
    raise ValueError("boom explosion")


def _make_agent(model, tools, *, max_no_progress: int = 5):
    """按 plan §5.2 顺序组装完整 5-middleware 链（Summary 阈值调大避免干扰）。"""
    return create_agent(
        model=model,
        tools=list(tools),
        state_schema=TaskState,
        middleware=[
            mw.Clarification(),
            mw.ToolErrorHandling(),
            mw.LoopDetection(max_no_progress=max_no_progress),
            mw.Summary(model, trigger=("messages", 100000), keep=("messages", 50000)),
            mw.ModelLengthFinishReason(),
        ],
        checkpointer=InMemorySaver(),
    )


def _run_ainvoke(agent, thread: str):
    config = {"configurable": {"thread_id": thread}}

    async def run():
        await agent.ainvoke(
            {"messages": [HumanMessage(content="go")], "goal": "g", "workspace": "/ws"},
            config,
        )
        return await agent.aget_state(config)

    return asyncio.run(run())


def _collect_interrupts(chunks: list[dict]) -> list:
    out = []
    for ch in chunks:
        for node, val in ch.items():
            if node == "__interrupt__":
                out.append(val)
    return out


def _contents(state) -> list[str]:
    return [str(m.content) for m in state.values["messages"]]


# --------------------------------------------------------------------------- #
# 1. ToolErrorHandling
# --------------------------------------------------------------------------- #
class TestToolErrorHandling:
    def test_boom_becomes_error_message_and_run_continues(self):
        model = _stub_model(
            AIMessage(content="", tool_calls=[_tool_call("boom", {}, "tc_boom")]),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [boom])
        state = _run_ainvoke(agent, "t-mw-err-1")

        contents = _contents(state)
        assert "done" in contents, f"run 应继续到完成：{contents}"
        error_msgs = [
            m for m in state.values["messages"]
            if isinstance(m, ToolMessage) and m.status == "error"
        ]
        assert error_msgs, "应有 status=error 的 ToolMessage"


# --------------------------------------------------------------------------- #
# 2. LoopDetection
# --------------------------------------------------------------------------- #
class TestLoopDetection:
    def test_stops_after_max_repeated_signature(self):
        """相同 add 调用连续 2 次后：第 3 次被拦截，工具不再执行，run 提前结束。

        注意：每次模型输出必须是**独立对象 + 独立 tool_call id**（真实 LLM 行为）。
        若复用同一 AIMessage 对象，add_messages 按 id 去重会吞掉后续消息，测试失真。
        """
        rec_add, calls = _make_rec_add()
        model = _stub_model(
            AIMessage(content="", tool_calls=[_tool_call("rec_add", {"a": 1, "b": 2}, "tc1")]),
            AIMessage(content="", tool_calls=[_tool_call("rec_add", {"a": 1, "b": 2}, "tc2")]),
            AIMessage(content="", tool_calls=[_tool_call("rec_add", {"a": 1, "b": 2}, "tc3")]),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [rec_add], max_no_progress=2)
        state = _run_ainvoke(agent, "t-mw-loop-1")

        assert calls == [(1, 2), (1, 2)], f"相同签名第 3 次不应执行：{calls}"
        assert "done" not in _contents(state), "应提前结束，不消费 done"

    def test_distinct_calls_do_not_loop(self):
        """不同参数 → 计数重置，全部执行后正常完成。"""
        rec_add, calls = _make_rec_add()
        model = _stub_model(
            AIMessage(content="", tool_calls=[_tool_call("rec_add", {"a": 1, "b": 2}, "tc1")]),
            AIMessage(content="", tool_calls=[_tool_call("rec_add", {"a": 3, "b": 4}, "tc2")]),
            AIMessage(content="", tool_calls=[_tool_call("rec_add", {"a": 5, "b": 6}, "tc3")]),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [rec_add], max_no_progress=2)
        state = _run_ainvoke(agent, "t-mw-loop-2")

        assert calls == [(1, 2), (3, 4), (5, 6)], f"不同签名应全部执行：{calls}"
        assert "done" in _contents(state)


# --------------------------------------------------------------------------- #
# 3. Clarification（全链内验证 interrupt + resume）
# --------------------------------------------------------------------------- #
class TestClarification:
    def test_interrupt_in_full_chain(self):
        model = _stub_model(
            AIMessage(content="", tool_calls=[
                _tool_call("ask_clarification", {"question": "整理哪个目录?"}, "tc_q")
            ]),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [ask_clarification])
        config = {"configurable": {"thread_id": "t-mw-clar-1"}}

        async def run():
            out = []
            async for chunk in agent.astream(
                {"messages": [HumanMessage(content="organize")]}, config, stream_mode="updates"
            ):
                out.append(chunk)
            return out

        chunks = asyncio.run(run())
        interrupts = _collect_interrupts(chunks)
        assert len(interrupts) == 1, f"应恰好中断一次：{interrupts}"
        actions = interrupts[0][0].value["action_requests"]
        assert actions[0]["name"] == "ask_clarification"

    def test_resume_after_interrupt_continues(self):
        model = _stub_model(
            AIMessage(content="", tool_calls=[
                _tool_call("ask_clarification", {"question": "整理哪个目录?"}, "tc_q")
            ]),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [ask_clarification])
        config = {"configurable": {"thread_id": "t-mw-clar-2"}}

        async def first():
            async for _ in agent.astream(
                {"messages": [HumanMessage(content="organize")]}, config, stream_mode="updates"
            ):
                pass

        async def resume():
            out = []
            async for chunk in agent.astream(
                Command(resume={"decisions": [{"type": "respond", "message": "目录a"}]}),
                config,
                stream_mode="updates",
            ):
                out.append(chunk)
            return await agent.aget_state(config)

        asyncio.run(first())
        state = asyncio.run(resume())
        contents = _contents(state)
        assert "目录a" in contents, f"respond 应注入用户回答：{contents}"
        assert "done" in contents, f"模型应继续回答：{contents}"


# --------------------------------------------------------------------------- #
# 4. Summary
# --------------------------------------------------------------------------- #
class TestSummary:
    def test_before_model_triggers_compression(self):
        summary = mw.Summary(
            _stub_model(AIMessage(content="汇总")),
            trigger=("messages", 2),
            keep=("messages", 1),
        )
        state = {
            "messages": [
                HumanMessage(content="h1"),
                AIMessage(content="a1"),
                HumanMessage(content="h2"),
                AIMessage(content="a2"),
            ]
        }
        updates = summary.before_model(state, None)

        assert updates and "messages" in updates, f"应产出消息更新：{updates}"
        msgs = updates["messages"]
        assert isinstance(msgs[0], RemoveMessage), "首个更新应为 RemoveMessage"
        summarized = [m for m in msgs if isinstance(m, HumanMessage)]
        assert summarized and any("汇总" in str(m.content) for m in summarized), (
            f"应注入摘要消息：{msgs}"
        )

    def test_below_threshold_noop(self):
        summary = mw.Summary(
            _stub_model(AIMessage(content="汇总")),
            trigger=("messages", 100),
            keep=("messages", 1),
        )
        state = {"messages": [HumanMessage(content="h1"), AIMessage(content="a1")]}
        assert summary.before_model(state, None) is None


# --------------------------------------------------------------------------- #
# 5. ModelLengthFinishReason
# --------------------------------------------------------------------------- #
class TestModelLengthFinishReason:
    def test_length_rejects_tools(self):
        """finish_reason=length → 整批拒绝执行工具，run 提前结束。"""
        rec_add, calls = _make_rec_add()
        model = _stub_model(
            AIMessage(
                content="truncated...",
                tool_calls=[_tool_call("rec_add", {"a": 1, "b": 2}, "tc1")],
                response_metadata={"finish_reason": "length"},
            ),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [rec_add])
        state = _run_ainvoke(agent, "t-mw-len-1")

        assert calls == [], f"截断时应拒绝执行工具：{calls}"
        contents = _contents(state)
        assert "truncated..." in contents
        assert "done" not in contents, "截断后不应继续调模型"

    def test_stop_allows_tools(self):
        """finish_reason=stop → 正常执行工具并完成。"""
        rec_add, calls = _make_rec_add()
        model = _stub_model(
            AIMessage(
                content="",
                tool_calls=[_tool_call("rec_add", {"a": 1, "b": 2}, "tc1")],
                response_metadata={"finish_reason": "stop"},
            ),
            AIMessage(content="done"),
        )
        agent = _make_agent(model, [rec_add])
        state = _run_ainvoke(agent, "t-mw-len-2")

        assert calls == [(1, 2)], f"stop 时应正常执行：{calls}"
        assert "done" in _contents(state)


# --------------------------------------------------------------------------- #
# 6. build_middleware 组装
# --------------------------------------------------------------------------- #
class TestBuildMiddleware:
    def test_build_returns_five_distinct(self):
        chain = mw.build_middleware(_stub_model(AIMessage(content="ok")))
        names = {m.name for m in chain}
        assert names == {
            "ToolErrorHandling",
            "LoopDetection",
            "Clarification",
            "Summary",
            "ModelLengthFinishReason",
        }
        # after_model 链反向执行（list 末尾最先跑）：ModelLength 截断先拒 → Clarification 澄清收尾
        idx = {m.name: i for i, m in enumerate(chain)}
        assert idx["ModelLengthFinishReason"] > idx["LoopDetection"] > idx["Clarification"]
