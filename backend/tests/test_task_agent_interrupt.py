"""Phase 2a 关键路径验证：clarify 打断（langchain 1.3 原生 HITL middleware）。

结论（已实证，本测试即回归锁定）：
- deer-flow 的 `wrap_tool_call 返回 Command(goto=END)` 模式在
  langchain 1.3.14 + langgraph 1.2.10 下【不生效】：tools 节点返回的
  Command 其 goto 会被 tools→model 条件边覆盖，循环继续回模型；若模型
  再次发出已被 ToolMessage 答复的工具调用，model_to_tools 第 6 分支
  （人工注入工具消息）返回 'model'，而该分支注册的 ends 只有
  {'tools','__end__'}（'model' 仅在有 after_model middleware 时才注册），
  直接 KeyError: 'model'。
- 正确方案 = langchain 原生 HumanInTheLoopMiddleware：
  它在 after_model 钩子里调 interrupt()，graph 真正暂停并产出
  `__interrupt__`；恢复用 `Command(resume={"decisions":[respond(...)]})`，
  respond 决策注入合成 ToolMessage，模型继续。

这是 Phase 2a 验收「打断生效」的基础：SSE 收到 __interrupt__ → 发
clarify_requested → 前端取用户回答 → POST /interrupt 恢复。
"""
import asyncio
from typing import Any, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 测试脚手架：stub 模型 + ask_clarification 工具 + 原生 HITL middleware
# --------------------------------------------------------------------------- #
class _StubModel(BaseChatModel, BaseModel):
    """stub：按序返回 responses；只 emit AIMessage（模型节点输出规则）。"""

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


@tool
def ask_clarification(question: str, options: Optional[list[str]] = None) -> str:
    """需要用户澄清时调用。真实返回由 HITL respond 决策注入，工具本身不执行。"""
    return ""


@tool
def add(a: int, b: int) -> int:
    """加法。用于验证非打断工具自动放行（不进 interrupt）。"""
    return a + b


def _hitl() -> HumanInTheLoopMiddleware:
    return HumanInTheLoopMiddleware(
        interrupt_on={
            "ask_clarification": {
                "allowed_decisions": ["respond"],
            }
        }
    )


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _collect_interrupts(chunks: list[dict]) -> list:
    """从 astream(stream_mode='updates') 收敛所有 __interrupt__ 值。"""
    out = []
    for ch in chunks:
        for node, val in ch.items():
            if node == "__interrupt__":
                out.append(val)
    return out


def _collect_messages(chunks: list[dict]) -> list[BaseMessage]:
    """把各种 chunk 形状收敛成消息列表（保留顺序，去重 by id）。"""
    seen_ids: set[str] = set()
    out: list[BaseMessage] = []

    def _push(msg: BaseMessage):
        mid = getattr(msg, "id", None)
        if mid and mid in seen_ids:
            return
        if mid:
            seen_ids.add(mid)
        out.append(msg)

    for ch in chunks:
        if not isinstance(ch, dict):
            continue
        for val in ch.values():
            if isinstance(val, dict) and isinstance(val.get("messages"), list):
                # updates 模式下节点输出形如 {'messages': [msg, ...]}
                for item in val["messages"]:
                    if isinstance(item, BaseMessage):
                        _push(item)
            elif isinstance(val, BaseMessage):
                _push(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, BaseMessage):
                        _push(item)
    return out


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #
class TestClarifyInterrupt:
    def test_interrupt_fires_on_clarify(self):
        """模型请求澄清 → graph 必须真正暂停，产出 __interrupt__，不再继续调模型。"""
        model = _stub_model(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "id": "tc1",
                        "args": {"question": "整理哪个目录?", "options": ["a", "b"]},
                    }
                ],
            ),
            AIMessage(content="done"),  # 若被打断，此响应不应被消费
        )
        agent = create_agent(
            model=model,
            tools=[ask_clarification],
            middleware=[_hitl()],
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "t-hitl-1"}}

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
        req = interrupts[0][0].value  # Interrupt.value = HITLRequest
        actions = req["action_requests"]
        assert len(actions) == 1
        assert actions[0]["name"] == "ask_clarification"
        assert actions[0]["args"]["question"] == "整理哪个目录?"
        # 模型第二响应未被消费：interrupt 后不应有 'done'
        contents = [str(m.content) for m in _collect_messages(chunks)]
        assert "done" not in contents, f"打断后不应继续跑模型：{contents}"

    def test_auto_approve_skips_interrupt(self):
        """非打断工具自动放行：ask_clarification + add 一起调用时只对前者打断。"""
        model = _stub_model(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "id": "tc1",
                        "args": {"question": "哪个?", "options": ["x"]},
                    },
                    {"name": "add", "id": "tc2", "args": {"a": 1, "b": 2}},
                ],
            ),
        )
        agent = create_agent(
            model=model,
            tools=[ask_clarification, add],
            middleware=[_hitl()],
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "t-hitl-2"}}

        async def run():
            out = []
            async for chunk in agent.astream(
                {"messages": [HumanMessage(content="organize")]}, config, stream_mode="updates"
            ):
                out.append(chunk)
            return out

        chunks = asyncio.run(run())
        interrupts = _collect_interrupts(chunks)
        assert len(interrupts) == 1
        actions = interrupts[0][0].value["action_requests"]
        names = [a["name"] for a in actions]
        assert names == ["ask_clarification"], f"只应打断 ask_clarification：{names}"

    def test_resume_respond_continues(self):
        """打断后：Command(resume=respond) 注入用户回答，模型继续执行剩余工具。"""
        model = _stub_model(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "id": "tc1",
                        "args": {"question": "整理哪个目录?", "options": ["a", "b"]},
                    },
                    {"name": "add", "id": "tc2", "args": {"a": 1, "b": 2}},
                ],
            ),
            AIMessage(content="done"),
        )
        agent = create_agent(
            model=model,
            tools=[ask_clarification, add],
            middleware=[_hitl()],
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "t-hitl-3"}}

        async def first_run():
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
            return out

        asyncio.run(first_run())
        chunks = asyncio.run(resume())

        contents = [str(m.content) for m in _collect_messages(chunks)]
        assert "目录a" in contents, f"respond 决策应注入用户回答：{contents}"
        assert "3" in contents, f"add 工具应真实执行：{contents}"
        assert "done" in contents, f"模型应继续回答：{contents}"
