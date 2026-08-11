"""Phase 2a：hooks.py 事件双落 + graph.py 组装 agent 测试（TDD，plan §5.2/§5.5）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_hooks_graph.py -q --basetemp=./.pytest-tmp

覆盖：
- EventBus：emit 双落（SQLite task_events + JSONL event 条目）+ 广播订阅者 + seq 单调跨实例。
- ToolCallEventMiddleware：全链内工具调用前后发射 tool_call/tool_result；异常 → is_error=True。
- build_agent：端到端（写文件落 workspace、checkpoint 落盘）、工具白名单、checkpoint 跨重建恢复、
  system prompt 工具铁律。
"""
import asyncio
import os
from typing import Any, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import graph, hooks, models
from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig


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


def _stub(*responses: BaseMessage) -> _StubModel:
    return _StubModel(responses=list(responses))


def _tc(name: str, args: dict, cid: str) -> dict:
    return {"name": name, "id": cid, "args": args}


@tool
def boom(x: str = "") -> str:
    """故意抛异常，验证 tool_result is_error=True。"""
    raise ValueError("boom explosion")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离元数据库 + 已建任务 + 临时 workspace（conf_bridge._cached 指向临时库）。"""
    import src.open_llm_vtuber.task_platform.conf_bridge as cb

    db = tmp_path / "meta.db"
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(cb, "_cached", TaskPlatformConfig(db_path=str(db)))
    models.init_db()
    task = models.create_task(title="t", workspace=str(ws), goal="g")
    return {"db": db, "ws": ws, "task_id": task.id, "cfg": cb.task_config()}


def _drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# --------------------------------------------------------------------------- #
# 1. EventBus：双落 + 广播 + seq
# --------------------------------------------------------------------------- #
class TestEventBus:
    def test_emit_dual_persists_and_broadcasts(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            bus = hooks.EventBus(ws, tid, run_id="r1")
            q = bus.subscribe()
            ev = bus.emit_status("running", "started")

            assert ev.seq == 1
            assert ev.event_type == "status"
            # 广播给订阅者
            got = q.get_nowait()
            assert got.seq == 1 and got.event_type == "status" and got.payload["state"] == "running"
            # SQLite 权威（SSE 重连锚点）
            replayed = models.events_after(tid, 0)
            assert [e.event_type for e in replayed] == ["status"]
            assert replayed[0].payload["state"] == "running"
            # JSONL 兜底
            jsonl_events = bus.session.read_events()
            assert any(e.get("seq") == 1 for e in jsonl_events)

        asyncio.run(scenario())

    def test_seq_monotonic_across_instances(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            a = hooks.EventBus(ws, tid, run_id="r1")
            a.emit_status("a")
            a.emit_status("b")
            b = hooks.EventBus(ws, tid, run_id="r2")  # 新实例续接（SQLite 权威锚）
            ev = b.emit_status("c")
            assert ev.seq == 3

        asyncio.run(scenario())

    def test_tool_event_payloads_match_contract(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            bus = hooks.EventBus(ws, tid, run_id="r1")
            bus.emit_tool_call("write_file", {"path": "a.txt", "content": "x"}, "tc1")
            bus.emit_tool_result("write_file", "ok", is_error=False, tool_call_id="tc1")
            evs = models.events_after(tid, 0)
            assert [e.event_type for e in evs] == ["tool_call", "tool_result"]
            assert evs[0].payload == {
                "name": "write_file",
                "arguments": {"path": "a.txt", "content": "x"},
                "tool_call_id": "tc1",
            }
            assert evs[1].payload == {
                "name": "write_file",
                "result": "ok",
                "is_error": False,
                "tool_call_id": "tc1",
            }

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 2. ToolCallEventMiddleware：全链内工具事件
# --------------------------------------------------------------------------- #
class TestToolCallEvents:
    def test_agent_emits_tool_call_and_result(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            model = _stub(
                AIMessage(content="", tool_calls=[_tc("write_file", {"path": "out.txt", "content": "hi"}, "tc1")]),
                AIMessage(content="done"),
            )
            bus = hooks.EventBus(ws, tid, run_id="r-events")
            q = bus.subscribe()
            async with graph.build_agent(ws, tid, bus=bus, cfg=env["cfg"], model=model) as agent:
                await agent.ainvoke(
                    {"messages": [HumanMessage(content="写文件")], "goal": "g", "workspace": ws},
                    {"configurable": {"thread_id": "t-ev-1"}},
                )

            events = _drain(q)
            assert [e.event_type for e in events] == ["tool_call", "tool_result"], [e.event_type for e in events]
            assert events[0].payload["name"] == "write_file"
            assert events[0].payload["tool_call_id"] == "tc1"
            assert events[1].payload["is_error"] is False
            assert "out.txt" in events[1].payload["result"]
            # 双落可回放
            replayed = models.events_after(tid, 0)
            assert [e.event_type for e in replayed] == ["tool_call", "tool_result"]
            # 工具真实执行（沙箱内）
            assert (env["ws"] / "out.txt").read_text(encoding="utf-8") == "hi"

        asyncio.run(scenario())

    def test_tool_error_emits_is_error_true(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            model = _stub(
                AIMessage(content="", tool_calls=[_tc("boom", {}, "tc-b")]),
                AIMessage(content="done"),
            )
            bus = hooks.EventBus(ws, tid, run_id="r-err")
            q = bus.subscribe()
            async with graph.build_agent(
                ws, tid, bus=bus, cfg=env["cfg"], model=model, extra_tools=(boom,)
            ) as agent:
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="boom")], "goal": "g", "workspace": ws},
                    {"configurable": {"thread_id": "t-ev-2"}},
                )

            events = _drain(q)
            assert [e.event_type for e in events] == ["tool_call", "tool_result"]
            assert events[1].payload["is_error"] is True
            assert "boom" in events[1].payload["result"]
            contents = [str(m.content) for m in state["messages"]]
            assert "done" in contents, "run 应继续完成"

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3. build_agent：组装 + 端到端 + checkpoint
# --------------------------------------------------------------------------- #
class TestGraphAgent:
    def test_build_agent_runs_end_to_end(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            model = _stub(
                AIMessage(content="", tool_calls=[_tc("write_file", {"path": "out.txt", "content": "hi"}, "tc1")]),
                AIMessage(content="done"),
            )
            async with graph.build_agent(ws, tid, cfg=env["cfg"], model=model) as agent:
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="写文件")], "goal": "g", "workspace": ws},
                    {"configurable": {"thread_id": "t-g-1"}},
                )
                contents = [str(m.content) for m in state["messages"]]
                assert "done" in contents
            assert (env["ws"] / "out.txt").read_text(encoding="utf-8") == "hi"
            assert graph.checkpoint_db_path(ws, tid).exists()

        asyncio.run(scenario())

    def test_build_agent_tools_are_sandbox_whitelist(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            model = _stub(AIMessage(content="ok"))
            async with graph.build_agent(ws, tid, cfg=env["cfg"], model=model) as agent:
                tool_node = agent.nodes["tools"].bound  # PregelNode.bound → ToolNode
                names = set(tool_node.tools_by_name.keys())
            # v3：sandbox 7 工具（+str_replace）+ web 2 工具 + skill 2 工具 + delegate
            # v4 C1：delegate_parallel 并入 delegate 组
            assert names == {"ls", "glob", "grep", "read_file", "write_file", "str_replace", "bash",
                             "web_search", "web_fetch",
                             "describe_skill", "read_skill", "delegate", "delegate_parallel"}, names

        asyncio.run(scenario())

    def test_checkpoint_persists_across_builds(self, env):
        """重建 agent（同 workspace/task_id）→ 上下文从 checkpoint 恢复。"""
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            model = _stub(
                AIMessage(content="", tool_calls=[_tc("write_file", {"path": "out.txt", "content": "hi"}, "tc1")]),
                AIMessage(content="done"),
            )
            config = {"configurable": {"thread_id": "t-g-2"}}
            async with graph.build_agent(ws, tid, cfg=env["cfg"], model=model) as agent:
                await agent.ainvoke(
                    {"messages": [HumanMessage(content="第一轮")], "goal": "g", "workspace": ws},
                    config,
                )
            async with graph.build_agent(ws, tid, cfg=env["cfg"], model=model) as agent2:
                state = await agent2.aget_state(config)
                contents = [str(m.content) for m in state.values["messages"]]
            assert "第一轮" in contents
            assert "done" in contents

        asyncio.run(scenario())

    def test_core_system_prompt_hard_constraint(self):
        sp = graph.CORE_SYSTEM_PROMPT
        assert "ls/glob/grep/read_file/write_file" in sp, "文件工具铁律必须在 system prompt"
        assert "bash" in sp
        assert "最后手段" in sp
        assert "/workspace" in sp

    def test_checkpoint_path_shape(self):
        p = graph.checkpoint_db_path("D:/ws", "t1")
        suffix = os.path.join(".pi", "tasks", "t1", "checkpoints.db")
        assert str(p).replace("\\", "/").endswith(suffix.replace("\\", "/"))
