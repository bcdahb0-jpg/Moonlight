"""Phase 6a：上下文压缩测试（plan §5.4 / §8 Phase 6 验收：压缩后长任务可续跑）。

覆盖：
- compact_task_context：长对话压缩后 checkpoint 缩为 [摘要, 保留尾]，JSONL 写 compaction 条目；
  摘要生成失败 → 在任何写入前抛错（JSONL + checkpoint 均不动）。
- 低于阈值 → no-op（不写条目、不改 checkpoint）。
- 压缩后可用完整 build_agent 续跑（验收核心）。
- 路由 POST /api/tasks/{id}/compact：成功 / 404 / 400。
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import graph, models, task_route
from src.open_llm_vtuber.task_platform.compaction import compact_task_context
from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig
from src.open_llm_vtuber.task_platform.graph import checkpoint_db_path
from src.open_llm_vtuber.task_platform.session import SessionFile
from src.open_llm_vtuber.task_platform.state import TaskState


# --------------------------------------------------------------------------- #
# 脚手架
# --------------------------------------------------------------------------- #
class _StubModel(BaseChatModel, BaseModel):
    responses: list[BaseMessage] = Field(default_factory=list)
    i: int = 0
    raise_on_invoke: bool = False

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.raise_on_invoke:
            raise RuntimeError("model down")
        n = self.i
        self.i += 1
        msg = self.responses[n % len(self.responses)] if self.responses else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _stub_model(*responses: BaseMessage) -> _StubModel:
    return _StubModel(responses=list(responses))


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

    for ctx in list(task_route._runs.values()):
        if ctx.run_task is not None and not ctx.run_task.done():
            ctx.run_task.cancel()
    task_route._runs.clear()
    task_route._buses.clear()


async def _seed_checkpoint(ws, tid, n_pairs=15):
    """直接 aupdate_state 追加 n_pairs 轮 Human/AI 消息（确定性，绕开 ainvoke 去重）。"""
    db = checkpoint_db_path(ws, tid)
    db.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        agent = create_agent(
            model=_stub_model(),  # 仅建图，不调用模型
            tools=[],
            state_schema=TaskState,
            middleware=[],
            checkpointer=saver,
        )
        msgs: list[BaseMessage] = []
        for i in range(n_pairs):
            msgs.append(HumanMessage(content=f"user-{i}"))
            msgs.append(AIMessage(content=f"assistant-{i}"))
        await agent.aupdate_state(
            {"configurable": {"thread_id": tid}},
            {"messages": msgs, "goal": "g", "workspace": ws},
        )


async def _read_checkpoint_messages(ws, tid, model=None):
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_db_path(ws, tid))) as saver:
        agent = create_agent(
            model=model or _stub_model(),
            tools=[],
            state_schema=TaskState,
            middleware=[],
            checkpointer=saver,
        )
        snap = await agent.aget_state({"configurable": {"thread_id": tid}})
        return list((snap.values or {}).get("messages") or [])


def _compaction_entries(ws, tid) -> list[dict]:
    return [e for e in SessionFile(ws, tid).iter_entries() if e.get("type") == "compaction"]


# --------------------------------------------------------------------------- #
# compact_task_context
# --------------------------------------------------------------------------- #
class TestCompactTaskContext:
    def test_compacts_long_conversation(self, env):
        async def scenario():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=15)  # 30 条消息
            stub = _stub_model(AIMessage(content="摘要：旧对话已压缩"))
            result = await compact_task_context(
                models.get_task(tid), env["cfg"], model=stub, keep_messages=10, keep_tokens=0
            )
            assert result["compacted"] is True
            assert result["removed"] == 20
            assert result["kept"] == 10
            assert "摘要" in result["summary"]
            # checkpoint 缩为 [摘要, 保留 10]
            msgs = await _read_checkpoint_messages(ws, tid, model=stub)
            assert len(msgs) == 11
            assert msgs[0].content == "Here is a summary of the conversation to date:\n\n摘要：旧对话已压缩"
            assert msgs[-1].content == "assistant-14", "保留尾不变"
            # JSONL 有 compaction 条目
            comps = _compaction_entries(ws, tid)
            assert len(comps) == 1
            assert "摘要" in comps[0]["summary"]
            assert comps[0]["firstKeptEntryId"], "锚定首个保留消息"

        asyncio.run(scenario())

    def test_below_threshold_noop(self, env):
        async def scenario():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=2)  # 4 条
            stub = _stub_model(AIMessage(content="x"))
            result = await compact_task_context(
                models.get_task(tid), env["cfg"], model=stub, keep_messages=10, keep_tokens=0
            )
            assert result["compacted"] is False
            assert result["reason"] == "below_threshold"
            # checkpoint 未变
            msgs = await _read_checkpoint_messages(ws, tid, model=stub)
            assert len(msgs) == 4
            # 无 compaction 条目
            assert _compaction_entries(ws, tid) == []

        asyncio.run(scenario())

    def test_empty_checkpoint_noop(self, env):
        async def scenario():
            tid, ws = env["task_id"], str(env["ws"])
            stub = _stub_model(AIMessage(content="x"))
            result = await compact_task_context(
                models.get_task(tid), env["cfg"], model=stub, keep_messages=10, keep_tokens=0
            )
            assert result["compacted"] is False
            assert _compaction_entries(ws, tid) == []

        asyncio.run(scenario())

    def test_model_failure_no_writes(self, env):
        """摘要生成失败 → 在任何写入前抛错（JSONL + checkpoint 均不动）。"""
        async def scenario():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=15)
            stub = _stub_model()
            stub.raise_on_invoke = True
            with pytest.raises(RuntimeError):
                await compact_task_context(
                    models.get_task(tid), env["cfg"], model=stub, keep_messages=10, keep_tokens=0
                )
            assert _compaction_entries(ws, tid) == [], "失败不写成功 compaction 条目"
            msgs = await _read_checkpoint_messages(ws, tid, model=stub)
            assert len(msgs) == 30, "失败不改 checkpoint"

        asyncio.run(scenario())

    def test_checkpoint_write_failure_marks_audit(self, env, monkeypatch):
        """checkpoint 写入失败 → JSONL 有 compaction_failed 标记（审计可辨非半成品）。"""
        import src.open_llm_vtuber.task_platform.compaction as comp_mod

        async def scenario():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=15)
            stub = _stub_model(AIMessage(content="摘要：旧对话已压缩"))

            real_create = comp_mod.create_agent

            class _FailingAgent:
                def __init__(self, real):
                    self._real = real

                def aget_state(self, config):
                    return self._real.aget_state(config)

                async def aupdate_state(self, *a, **k):
                    raise RuntimeError("boom checkpoint write")

            def _failing_create(*a, **k):
                return _FailingAgent(real_create(*a, **k))

            monkeypatch.setattr(comp_mod, "create_agent", _failing_create)
            with pytest.raises(RuntimeError, match="boom checkpoint write"):
                await compact_task_context(
                    models.get_task(tid), env["cfg"], model=stub, keep_messages=10, keep_tokens=0
                )
            # 审计可辨：成功 compaction 条目已被失败标记"父指向"，失败分支显式记录
            entries = list(SessionFile(ws, tid).iter_entries())
            comps = [e for e in entries if e.get("type") == "compaction"]
            fails = [e for e in entries if e.get("type") == "compaction_failed"]
            assert len(comps) == 1 and len(fails) == 1
            assert fails[0]["parentId"] == comps[0]["id"], "失败标记指向原 compaction 条目"
            assert "boom checkpoint write" in fails[0]["error"]
            # checkpoint 未变（压缩不生效）
            msgs = await _read_checkpoint_messages(ws, tid, model=stub)
            assert len(msgs) == 30

        asyncio.run(scenario())

    def test_resumable_after_compact(self, env):
        """验收：压缩后长任务可续跑（完整 build_agent 续接压缩后的 checkpoint）。"""
        async def scenario():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=15)
            stub = _stub_model(
                AIMessage(content="摘要：旧对话已压缩"),
                AIMessage(content="继续回答"),
            )
            result = await compact_task_context(
                models.get_task(tid), env["cfg"], model=stub, keep_messages=10, keep_tokens=0
            )
            assert result["compacted"] is True
            async with graph.build_agent(ws, tid, cfg=env["cfg"], model=stub) as agent:
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="继续做")], "goal": "g", "workspace": ws},
                    {"configurable": {"thread_id": tid}},
                )
            assert isinstance(state["messages"][-1], AIMessage), "续跑产生新 AI 回复"
            assert str(state["messages"][0].content).startswith("Here is a summary"), "摘要仍在前部"

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 路由 POST /api/tasks/{id}/compact
# --------------------------------------------------------------------------- #
class TestCompactRoute:
    def _client(self, env, monkeypatch):
        monkeypatch.setattr(task_route, "_is_local_request", lambda request: True)
        # 路由经 compaction.compact_task_context(task, task_config(), ...) 未传 model →
        # 内部走 graph.build_model(cfg)。测试环境 cfg 无 LLM 快照，注入 stub 返回摘要。
        monkeypatch.setattr(
            graph,
            "build_model",
            lambda cfg: _stub_model(AIMessage(content="摘要：旧对话已压缩")),
        )
        app = FastAPI()
        app.include_router(task_route.init_task_route())
        return TestClient(app)

    def test_success(self, env, monkeypatch):
        async def seed():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=10)  # 20 条

        asyncio.run(seed())
        client = self._client(env, monkeypatch)
        r = client.post(f"/api/tasks/{env['task_id']}/compact?keep=5")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["compacted"] is True
        assert body["kept"] == 5

    def test_not_found(self, env, monkeypatch):
        client = self._client(env, monkeypatch)
        r = client.post("/api/tasks/nonexistent/compact")
        assert r.status_code == 404
        assert r.json()["ok"] is False

    def test_invalid_keep(self, env, monkeypatch):
        client = self._client(env, monkeypatch)
        r = client.post(f"/api/tasks/{env['task_id']}/compact?keep=0")
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_conflict_when_run_running(self, env, monkeypatch):
        """运行中的 run → 409（压缩与 checkpoint 写入并发互相踩）。"""
        class _FakeTask:
            def done(self):
                return False

            def cancel(self):
                pass

        tid, ws = env["task_id"], str(env["ws"])
        ctx = task_route.RunContext(models.get_task(tid), None, None, env["cfg"])
        ctx.run_task = _FakeTask()
        task_route._runs[tid] = ctx
        try:
            client = self._client(env, monkeypatch)
            r = client.post(f"/api/tasks/{tid}/compact")
            assert r.status_code == 409
            assert r.json()["ok"] is False
        finally:
            task_route._runs.pop(tid, None)

    def test_keep_one_boundary(self, env, monkeypatch):
        async def seed():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=10)  # 20 条

        asyncio.run(seed())
        client = self._client(env, monkeypatch)
        r = client.post(f"/api/tasks/{env['task_id']}/compact?keep=1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["compacted"] is True
        assert body["kept"] == 1

    def test_large_keep_noop(self, env, monkeypatch):
        """keep 大于消息数 → no-op（below_threshold），不触发摘要调用。"""
        async def seed():
            tid, ws = env["task_id"], str(env["ws"])
            await _seed_checkpoint(ws, tid, n_pairs=2)  # 4 条

        asyncio.run(seed())
        client = self._client(env, monkeypatch)
        r = client.post(f"/api/tasks/{env['task_id']}/compact?keep=100")
        assert r.status_code == 200, r.text
        assert r.json()["compacted"] is False
        assert r.json()["reason"] == "below_threshold"
