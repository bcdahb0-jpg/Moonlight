"""Phase 6b：sub-agent 委派工具测试（plan §8 Phase 6b 验收：delegate 委派成功）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_agents.py -q --basetemp=./.pytest-tmp

覆盖（plan §8 Phase 6b）：
- frontmatter：`agents/*.md` 解析 + 契约（description 必填 / enabled/tools/prompt_mode 默认值）。
- catalog：扫描只保留 enabled: true；`*.disabled` 后缀跳过；非法定义跳过。
- delegate 工具：unknown → 错误文本提示可用列表；已知 → 临时子 agent 跑完返回最终文本。
- 工具收窄：`read` = 只读集（无 write_file/bash）；`all` = 全部沙箱工具。
- prompt_mode：replace = 正文即完整 prompt；append = 主 prompt + 正文。
- build_agent：include_agents 默认开 → delegate 并入工具；端到端可触发子 agent。
- graph.list_agents。
"""
import asyncio
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import graph
from src.open_llm_vtuber.task_platform.agents import catalog as agent_catalog
from src.open_llm_vtuber.task_platform.agents import frontmatter, tools as agent_tools


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

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        n = self.i
        self.i += 1
        msg = self.responses[n % len(self.responses)] if self.responses else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _write_agent(root: Path, name: str, *, body: str = "## 子任务\n完成委派。\n", **fm) -> Path:
    """写一个合法 `agents/<name>.md`。fm 为 frontmatter 覆盖项。"""
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{name}.md"
    data = {"description": f"测试子 agent {name}"}
    data.update(fm)
    lines = []
    for k, v in data.items():
        vv = str(v).lower() if isinstance(v, bool) else v
        lines.append(f"{k}: {vv}")
    p.write_text(f"---\n" + "\n".join(lines) + f"\n---\n{body}", encoding="utf-8")
    return p


def _delegate_tool(catalog, workspace: Path, model, base_prompt: str = "主 prompt"):
    tools = agent_tools.agent_tools(
        catalog,
        workspace=str(workspace),
        model=model,
        base_prompt=base_prompt,
    )
    return tools[0]  # delegate（index 0；index 1 是 delegate_parallel）


def _parallel_tool(catalog, workspace: Path, model, base_prompt: str = "主 prompt", parallel_max: int = 3):
    tools = agent_tools.agent_tools(
        catalog,
        workspace=str(workspace),
        model=model,
        base_prompt=base_prompt,
        parallel_max=parallel_max,
    )
    return tools[1]


# --------------------------------------------------------------------------- #
# 1. frontmatter 解析 + 契约
# --------------------------------------------------------------------------- #
class TestFrontmatter:
    def test_parse_valid(self, tmp_path):
        p = _write_agent(tmp_path, "explore", tools="read", prompt_mode="replace", display_name="E")
        fm, body = frontmatter.parse_agent_md(p)
        assert fm["description"] == "测试子 agent explore"
        assert fm["enabled"] is True
        assert fm["tools"] == "read"
        assert fm["prompt_mode"] == "replace"
        assert fm["display_name"] == "E"
        assert body.startswith("## 子任务")

    def test_defaults(self, tmp_path):
        p = _write_agent(tmp_path, "worker")
        fm, _ = frontmatter.parse_agent_md(p)
        assert fm["enabled"] is True
        assert fm["tools"] == "read"  # 默认安全只读
        assert fm["prompt_mode"] == "append"

    def test_missing_description_rejected(self, tmp_path):
        p = _write_agent(tmp_path, "x", description="")
        assert frontmatter.parse_agent_md(p) is None

    def test_no_frontmatter_rejected(self, tmp_path):
        p = tmp_path / "plain.md"
        p.write_text("# 没有 frontmatter\n", encoding="utf-8")
        assert frontmatter.parse_agent_md(p) is None

    def test_bad_prompt_mode_rejected(self, tmp_path):
        p = _write_agent(tmp_path, "bad", prompt_mode="overwrite")
        assert frontmatter.parse_agent_md(p) is None


# --------------------------------------------------------------------------- #
# 2. catalog
# --------------------------------------------------------------------------- #
class TestCatalog:
    def test_scan_enabled_only(self, tmp_path):
        _write_agent(tmp_path, "explore", tools="read")
        _write_agent(tmp_path, "worker", tools="all")
        _write_agent(tmp_path, "arch", enabled=False)
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        assert set(cat.names()) == {"explore", "worker"}
        assert cat.get("worker").tools == "all"

    def test_disabled_suffix_skipped(self, tmp_path):
        _write_agent(tmp_path, "old", enabled=True)
        (tmp_path / "old.disabled.md").write_text(
            "---\ndescription: 停用\n---\n正文\n", encoding="utf-8"
        )
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        assert cat.get("old") is not None
        assert "old.disabled" not in cat.names()

    def test_missing_root_empty(self, tmp_path):
        cat = agent_catalog.AgentCatalog(tmp_path / "nope").scan()
        assert cat.names() == []
        assert cat.list() == []


# --------------------------------------------------------------------------- #
# 3. delegate 工具
# --------------------------------------------------------------------------- #
class TestDelegateTool:
    def test_unknown_agent(self, tmp_path):
        _write_agent(tmp_path, "explore", tools="read")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        tool = _delegate_tool(cat, tmp_path, _StubModel())

        async def run():
            return await tool.ainvoke({"agent_name": "nope", "task": "x"})

        out = asyncio.run(run())
        assert "nope" in out and "explore" in out  # 错误文本提示可用列表

    def test_delegate_returns_sub_agent_text(self, tmp_path):
        _write_agent(tmp_path, "explore", tools="read", prompt_mode="replace")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        stub = _StubModel(responses=[AIMessage(content="找到 3 处引用")])
        tool = _delegate_tool(cat, tmp_path, stub)

        async def run():
            return await tool.ainvoke({"agent_name": "explore", "task": "查 find_all"})

        out = asyncio.run(run())
        assert out == "找到 3 处引用"

    def test_read_only_toolset(self, tmp_path):
        """explore（tools=read）子 agent 只含只读工具，无 write_file/bash。"""
        _write_agent(tmp_path, "explore", tools="read")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        sb_tools = _sandbox_tools(tmp_path)
        names = {t.name for t in agent_tools._resolve_tools("read", sb_tools)}
        assert names == {"ls", "glob", "grep", "read_file"}
        assert "write_file" not in names and "bash" not in names
        assert {t.name for t in agent_tools._resolve_tools("all", sb_tools)} >= {
            "ls", "glob", "grep", "read_file", "write_file", "bash"
        }

    def test_prompt_mode_build(self, tmp_path):
        """replace=正文完整；append=主 prompt 前缀 + 正文。"""
        _write_agent(tmp_path, "a", prompt_mode="replace", body="独立角色\n")
        _write_agent(tmp_path, "b", prompt_mode="append", body="追加指引\n")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        assert agent_tools._build_system_prompt(cat.get("a"), "主 prompt") == "独立角色"
        assert "主 prompt" in agent_tools._build_system_prompt(cat.get("b"), "主 prompt")
        assert "追加指引" in agent_tools._build_system_prompt(cat.get("b"), "主 prompt")


# --------------------------------------------------------------------------- #
# 3b. delegate_parallel 并行委派（v4 Phase C1）
# --------------------------------------------------------------------------- #
class TestDelegateParallel:
    def test_two_tools_registered(self, tmp_path):
        _write_agent(tmp_path, "a", tools="read")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        tools = agent_tools.agent_tools(cat, workspace=str(tmp_path), model=_StubModel(), base_prompt="p")
        assert [t.name for t in tools] == ["delegate", "delegate_parallel"]

    def test_parallel_returns_aggregated(self, tmp_path):
        _write_agent(tmp_path, "a", tools="read", prompt_mode="replace")
        _write_agent(tmp_path, "b", tools="read", prompt_mode="replace")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        stub = _StubModel(responses=[
            AIMessage(content="A 的结果"),  # a
            AIMessage(content="B 的结果"),  # b
        ])
        tool = _parallel_tool(cat, tmp_path, stub)

        async def run():
            return await tool.ainvoke({
                "delegations": [
                    {"agent_name": "a", "task": "任务一"},
                    {"agent_name": "b", "task": "任务二"},
                ]
            })

        out = asyncio.run(run())
        assert "[1] a: [done] A 的结果" in out
        assert "[2] b: [done] B 的结果" in out

    def test_unknown_branch_isolated(self, tmp_path):
        _write_agent(tmp_path, "a", tools="read")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        tool = _parallel_tool(cat, tmp_path, _StubModel())

        async def run():
            return await tool.ainvoke({
                "delegations": [
                    {"agent_name": "a", "task": "ok"},
                    {"agent_name": "ghost", "task": "x"},
                ]
            })

        out = asyncio.run(run())
        assert "[1] a: [done]" in out  # 正常分支不受影响
        assert "ghost: 不存在" in out  # 未知分支提示

    def test_empty_rejected(self, tmp_path):
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        tool = _parallel_tool(cat, tmp_path, _StubModel())
        out = asyncio.run(tool.ainvoke({"delegations": []}))
        assert "为空" in out

    def test_too_many_rejected(self, tmp_path):
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        tool = _parallel_tool(cat, tmp_path, _StubModel())
        many = [{"agent_name": "a", "task": f"t{i}"} for i in range(51)]
        out = asyncio.run(tool.ainvoke({"delegations": many}))
        assert "50" in out

    def test_semaphore_limits_concurrency(self, tmp_path, monkeypatch):
        """Semaphore 生效：并行 3 个、max_concurrency=1 → 峰值并发 1。"""
        _write_agent(tmp_path, "a", tools="read")
        cat = agent_catalog.AgentCatalog(tmp_path).scan()
        tool = _parallel_tool(cat, tmp_path, _StubModel(), parallel_max=1)
        info = cat.get("a")

        active = 0
        peak = 0

        async def fake_run(info, task, **kw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "done", f"result {task}"

        monkeypatch.setattr(agent_tools, "_run_delegate", fake_run)

        async def run():
            return await tool.ainvoke({
                "delegations": [
                    {"agent_name": "a", "task": "t1"},
                    {"agent_name": "a", "task": "t2"},
                    {"agent_name": "a", "task": "t3"},
                ]
            })

        out = asyncio.run(run())
        assert peak == 1  # 串行化
        assert out.count("[done]") == 3
        assert "[1] a: [done] result t1" in out


# --------------------------------------------------------------------------- #
# 4. build_agent 集成（delegate 并入主 agent 工具，端到端触发）
# --------------------------------------------------------------------------- #
class TestBuildAgentDelegate:
    def test_delegate_tool_injected(self, tmp_path, monkeypatch):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        _write_agent(tmp_path, "explore", tools="read", prompt_mode="replace")
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(
            cb, "_cached", cb.TaskPlatformConfig(db_path=str(tmp_path / "meta.db"),
                                                 agents_root=str(tmp_path))
        )
        stub = _StubModel(
            responses=[
                AIMessage(content="", tool_calls=[{
                    "name": "delegate",
                    "args": {"agent_name": "explore", "task": "查 find_all"},
                    "id": "c1",
                }]),
                AIMessage(content="完成委派"),
            ]
        )

        async def run():
            async with graph.build_agent(str(ws), "t1", cfg=cb.task_config(), model=stub) as agent:
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="请委派检索")],
                     "goal": "查 find_all", "workspace": str(ws)},
                    {"configurable": {"thread_id": "t1"}},
                )
            return state

        state = asyncio.run(run())
        texts = [str(m.content) for m in state["messages"]]
        assert any("找到" in t or "完成委派" in t for t in texts), texts


# --------------------------------------------------------------------------- #
# 5. list_agents + 路由
# --------------------------------------------------------------------------- #
class TestListAgents:
    def test_list_agents(self, tmp_path):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        _write_agent(tmp_path, "explore", tools="read", prompt_mode="replace")
        _write_agent(tmp_path, "worker", tools="all")
        _write_agent(tmp_path, "arch", enabled=False)
        cfg = cb.TaskPlatformConfig(agents_root=str(tmp_path))
        infos = graph.list_agents(cfg)
        assert [i["name"] for i in infos] == ["explore", "worker"]
        assert infos[0]["tools"] == "read"


class TestAgentsRoute:
    def test_list_agents_route(self, tmp_path, monkeypatch):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb
        import src.open_llm_vtuber.task_platform.task_route as task_route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        _write_agent(tmp_path, "explore", tools="read", prompt_mode="replace")
        monkeypatch.setattr(cb, "_cached", cb.TaskPlatformConfig(
            db_path=str(tmp_path / "meta.db"), agents_root=str(tmp_path)))
        monkeypatch.setattr(task_route, "_is_local_request", lambda request: True)
        app = FastAPI()
        app.include_router(task_route.init_task_route())
        r = TestClient(app).get("/api/agents")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert [a["name"] for a in body["agents"]] == ["explore"]

    def test_agents_route_forbidden_outside_local(self, tmp_path, monkeypatch):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb
        import src.open_llm_vtuber.task_platform.task_route as task_route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setattr(cb, "_cached", cb.TaskPlatformConfig(db_path=str(tmp_path / "meta.db")))
        monkeypatch.setattr(task_route, "_is_local_request", lambda request: False)
        app = FastAPI()
        app.include_router(task_route.init_task_route())
        r = TestClient(app).get("/api/agents")
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# 辅助（sandbox 工具构造）
# --------------------------------------------------------------------------- #
def _sandbox_tools(workspace: Path) -> list:
    from src.open_llm_vtuber.task_platform.sandbox import sandbox_tools

    return sandbox_tools(str(workspace))
