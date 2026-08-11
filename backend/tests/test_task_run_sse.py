"""Phase 2a：task_route run / SSE / interrupt 编排测试（TDD，plan §5.5 / §8）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_run_sse.py -q --basetemp=./.pytest-tmp

覆盖（对应 plan §8 验收）：
- start_run：完整 run 生命周期（后台 agent 执行 → run 表 completed、文件落盘、
  事件双落含 run_start/message/tool_call/tool_result/run_end）。
- SSE：断线/重启重连回放（`after_seq=N` 从 SQLite 锚点续播）；实时流完整事件序列。
- interrupt：取消后台 agent 任务 → run 状态 interrupted + run_end(status=interrupted)。
- 并发互斥：同任务已有运行中 run → RunBusyError（409 语义）。
"""
import asyncio
import contextlib
import json
from typing import Any, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import models, task_route
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


def _stub_model(*responses: BaseMessage) -> _StubModel:
    return _StubModel(responses=list(responses))


def _tc(name: str, args: dict, cid: str) -> dict:
    return {"name": name, "id": cid, "args": args}


@tool
async def slow_tool(delay: int = 60) -> str:
    """慢工具：长时间 await，供 interrupt 打断验证。"""
    await asyncio.sleep(delay)
    return "ok"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离元数据库 + 已建任务 + 临时 workspace；用后清理编排注册表。"""
    import src.open_llm_vtuber.task_platform.conf_bridge as cb

    db = tmp_path / "meta.db"
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(cb, "_cached", TaskPlatformConfig(db_path=str(db)))
    models.init_db()
    task = models.create_task(title="t", workspace=str(ws), goal="g")
    yield {"db": db, "ws": ws, "task_id": task.id, "cfg": cb.task_config()}

    # 清理编排注册表，避免跨测试泄漏（取消仍在跑的 run）
    for ctx in list(task_route._runs.values()):
        if ctx.run_task is not None and not ctx.run_task.done():
            ctx.run_task.cancel()
    task_route._runs.clear()
    task_route._buses.clear()


def _write_file_model():
    """写文件 → 完成（跑通白名单写文件）。"""
    return _stub_model(
        AIMessage(content="", tool_calls=[_tc("write_file", {"path": "out.txt", "content": "hi"}, "tc1")]),
        AIMessage(content="完成"),
    )


async def _wait_tool_call(tid: str, timeout: float = 5.0) -> None:
    """轮询等待 tool_call 事件落库（保证 agent 已进入工具执行，避免打断过早）。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if any(e.event_type == "tool_call" for e in models.events_after(tid, 0)):
            return
        await asyncio.sleep(0.01)
    pytest.fail("tool_call 事件未在超时前落库")


def _parse_sse(line: str) -> dict:
    assert line.startswith("data: "), f"非事件行：{line!r}"
    return json.loads(line[6:])


async def _collect_until_run_end(gen) -> list[dict]:
    """消费 SSE 生成器至 run_end，然后关闭（释放 bus 订阅）。"""
    got: list[dict] = []
    try:
        async for line in gen:
            if line.startswith("data: "):
                ev = _parse_sse(line)
                got.append(ev)
                if ev["event_type"] == "run_end":
                    break
    finally:
        await gen.aclose()
    return got


# --------------------------------------------------------------------------- #
# 1. start_run：run 生命周期
# --------------------------------------------------------------------------- #
class TestStartRun:
    def test_completes_run_and_persists_events(self, env):
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "写文件", model=_write_file_model()
            )
            assert ctx.is_running
            await ctx.run_task
            assert not ctx.is_running

            run = models.get_run(ctx.run.id)
            assert run is not None and run.status == "completed"

            evs = models.events_after(tid, 0)
            types = [e.event_type for e in evs]
            assert types == ["run_start", "message", "tool_call", "tool_result", "message", "run_end"], types
            assert evs[0].payload["goal"] == "g"
            assert evs[1].payload["role"] == "user" and evs[1].payload["content"] == "写文件"
            assert evs[2].payload["name"] == "write_file"
            assert evs[3].payload["is_error"] is False
            assert evs[4].payload["role"] == "assistant" and evs[4].payload["content"] == "完成"
            assert evs[5].payload["status"] == "completed"
            # 工具真实落盘（沙箱内）
            assert (env["ws"] / "out.txt").read_text(encoding="utf-8") == "hi"
            # 会话历史投影（JSONL message 条目，role/content 在 message 子对象内）
            session_msgs = ctx.bus.session.read_messages()
            assert any(m["message"].get("role") == "user" for m in session_msgs)
            assert any(m["message"].get("role") == "assistant" for m in session_msgs)

        asyncio.run(scenario())

    def test_rejects_second_run_while_busy(self, env):
        tid = env["task_id"]

        async def scenario():
            slow_model = _stub_model(
                AIMessage(content="", tool_calls=[_tc("slow_tool", {"delay": 60}, "tc-slow")]),
                AIMessage(content="done"),
            )
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "慢任务", model=slow_model, extra_tools=(slow_tool,)
            )
            await _wait_tool_call(tid)
            with pytest.raises(task_route.RunBusyError):
                task_route.start_run(models.get_task(tid), env["cfg"], "又来", model=slow_model, extra_tools=(slow_tool,))
            ctx.run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.run_task

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 2. SSE：回放 + 实时
# --------------------------------------------------------------------------- #
class TestSSE:
    def test_replay_full_stream_after_completed_run(self, env):
        """断线/重启重连：SSE 从 SQLite 锚点回放完整事件序列（run 已结束）。"""
        tid = env["task_id"]

        async def scenario():
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "写文件", model=_write_file_model()
            )
            await ctx.run_task

            events = await _collect_until_run_end(task_route._sse_events(tid, 0))
            types = [e["event_type"] for e in events]
            assert types == ["run_start", "message", "tool_call", "tool_result", "message", "run_end"], types

        asyncio.run(scenario())

    def test_after_seq_resumes_partial_stream(self, env):
        """断线带 after_seq=N 重连：只回放 seq>N 的增量（seq 单调锚）。"""
        tid = env["task_id"]

        async def scenario():
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "写文件", model=_write_file_model()
            )
            await ctx.run_task

            last = models.last_seq(tid)
            assert last >= 6
            assert models.events_after(tid, last) == [], "seq>last 应为空"
            # after_seq=1 → 从第 2 条开始回放（跳过 run_start）
            events = await _collect_until_run_end(task_route._sse_events(tid, 1))
            types = [e["event_type"] for e in events]
            assert types[0] == "message", f"after_seq=1 应从第 2 条开始回放：{types}"
            assert types[-1] == "run_end"

        asyncio.run(scenario())

    def test_live_stream_while_running(self, env):
        """实时流：SSE 先订阅，run 进行中事件实时广播（非回放）。"""
        ws, tid = str(env["ws"]), env["task_id"]

        async def scenario():
            # 先让 SSE 生成器订阅常驻 bus（此时无事件）
            collector = asyncio.create_task(_collect_until_run_end(task_route._sse_events(tid, 0)))
            await asyncio.sleep(0.05)

            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "写文件", model=_write_file_model()
            )
            events = await collector
            await ctx.run_task

            types = [e["event_type"] for e in events]
            assert types == ["run_start", "message", "tool_call", "tool_result", "message", "run_end"], types
            assert events[4]["payload"]["content"] == "完成"

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3. interrupt
# --------------------------------------------------------------------------- #
class TestInterrupt:
    def test_interrupt_marks_run_interrupted(self, env):
        tid = env["task_id"]

        async def scenario():
            slow_model = _stub_model(
                AIMessage(content="", tool_calls=[_tc("slow_tool", {"delay": 60}, "tc-slow")]),
                AIMessage(content="done"),
            )
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "慢任务", model=slow_model, extra_tools=(slow_tool,)
            )
            await _wait_tool_call(tid)  # agent 已进入工具 sleep（不会自己结束）

            ctx.run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.run_task

            run = models.get_run(ctx.run.id)
            assert run.status == "interrupted"
            run_end = [e for e in models.events_after(tid, 0) if e.event_type == "run_end"]
            assert run_end and run_end[0].payload["status"] == "interrupted"

        asyncio.run(scenario())

    def test_discard_task_state_cancels_run_and_cleans_registries(self, env):
        """删除任务时清理内存注册表（review HIGH）：取消跑中 run、丢弃 bus。"""
        tid = env["task_id"]

        async def scenario():
            slow_model = _stub_model(
                AIMessage(content="", tool_calls=[_tc("slow_tool", {"delay": 60}, "tc-slow")]),
                AIMessage(content="done"),
            )
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "慢任务", model=slow_model, extra_tools=(slow_tool,)
            )
            await _wait_tool_call(tid)
            assert tid in task_route._runs and tid in task_route._buses
            assert task_route._runs[tid].is_running

            task_route._discard_task_state(tid)
            assert tid not in task_route._runs, "删除后 _runs 应清空"
            assert tid not in task_route._buses, "删除后 _buses 应清空"

            with contextlib.suppress(asyncio.CancelledError):
                await ctx.run_task
            run = models.get_run(ctx.run.id)
            assert run.status == "interrupted", "清理时取消的 run 应落 interrupted 终态"

        asyncio.run(scenario())
