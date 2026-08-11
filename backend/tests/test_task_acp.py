"""v4 Phase C2：外部 ACP agent 接入测试（mock ACP 协议层，不起真实子进程）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_acp.py -q --basetemp=.pytest-tmp
"""
import asyncio
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.task_platform import acp_client
from src.open_llm_vtuber.task_platform.conf_bridge import AcpAgentConfig


# --------------------------------------------------------------------------- #
# 假 acp SDK（模拟 spawn_agent_process 协议交互）
# --------------------------------------------------------------------------- #
def _install_fake_acp(conn_cls=None, *, prompt_timeout=False):
    """注入假的 acp / acp.schema 模块到 sys.modules（与 test_task_web 同思路）。"""

    class FakeSchema:
        class TextContentBlock:
            def __init__(self, text=""):
                self.text = text

        @staticmethod
        def ClientCapabilities():
            return {}

        @staticmethod
        def Implementation(**kw):
            return kw

        class AllowedOutcome:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        class DeniedOutcome:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        class RequestPermissionResponse:
            def __init__(self, outcome=None):
                self.outcome = outcome

    class FakeConn:
        def __init__(self, client):
            self.client = client

        async def initialize(self, **kw):
            pass

        async def new_session(self, **kw):
            return SimpleNamespace(session_id="s1")

        async def prompt(self, **kw):
            if prompt_timeout:
                raise TimeoutError("timed out")
            # 模拟外部 agent 流式输出 → client.session_update
            block = SimpleNamespace(
                content=FakeSchema.TextContentBlock(text="外部 agent 完成：构建通过")
            )
            await self.client.session_update("s1", block)

    @asynccontextmanager
    async def fake_spawn(client, command, *args, env=None, cwd=None, **kw):
        yield (FakeConn(client), SimpleNamespace())

    mod = types.ModuleType("acp")
    mod.PROTOCOL_VERSION = 1
    mod.text_block = lambda x: {"type": "text", "text": x}
    mod.Client = object
    mod.spawn_agent_process = fake_spawn
    mod.RequestPermissionResponse = FakeSchema.RequestPermissionResponse  # acp 顶层 re-export
    schema_mod = types.ModuleType("acp.schema")
    schema_mod.TextContentBlock = FakeSchema.TextContentBlock
    schema_mod.ClientCapabilities = FakeSchema.ClientCapabilities
    schema_mod.Implementation = FakeSchema.Implementation
    schema_mod.RequestPermissionResponse = FakeSchema.RequestPermissionResponse
    schema_mod.AllowedOutcome = FakeSchema.AllowedOutcome
    schema_mod.DeniedOutcome = FakeSchema.DeniedOutcome
    sys.modules["acp"] = mod
    sys.modules["acp.schema"] = schema_mod
    return FakeSchema


def _cfg(agents=None):
    from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig

    return TaskPlatformConfig(acp_agents=agents or [])


# --------------------------------------------------------------------------- #
# 工具注册
# --------------------------------------------------------------------------- #
class TestToolRegistration:
    def test_empty_config_no_tool(self, tmp_path):
        tools = acp_client.acp_agent_tools(_cfg(), str(tmp_path), "t1")
        assert tools == []

    def test_tool_registered_with_agents(self, tmp_path):
        cfg = _cfg([AcpAgentConfig(name="codex", command="codex", args=["-acp"])])
        tools = acp_client.acp_agent_tools(cfg, str(tmp_path), "t1")
        assert [t.name for t in tools] == ["invoke_acp_agent"]
        assert "codex" in tools[0].description

    def test_workspace_dir_created(self, tmp_path):
        cfg = _cfg([AcpAgentConfig(name="claude", command="claude")])
        acp_client.acp_agent_tools(cfg, str(tmp_path), "t9")
        d = tmp_path / ".pi" / "tasks" / "t9" / "acp-workspace"
        assert d.is_dir()


# --------------------------------------------------------------------------- #
# 调用路径
# --------------------------------------------------------------------------- #
class TestInvoke:
    def test_success_collects_text(self, tmp_path, monkeypatch):
        _install_fake_acp()
        cfg = _cfg([AcpAgentConfig(name="codex", command="codex", args=["-acp"])])
        (tool,) = acp_client.acp_agent_tools(cfg, str(tmp_path), "t1")
        out = asyncio.run(tool.ainvoke({"agent": "codex", "prompt": "构建项目"}))
        assert "外部 agent 完成" in out

    def test_unknown_agent(self, tmp_path):
        _install_fake_acp()
        cfg = _cfg([AcpAgentConfig(name="codex", command="codex")])
        (tool,) = acp_client.acp_agent_tools(cfg, str(tmp_path), "t1")
        out = asyncio.run(tool.ainvoke({"agent": "nope", "prompt": "x"}))
        assert "[acp]" in out and "nope" in out and "codex" in out

    def test_timeout_returns_error_text(self, tmp_path):
        _install_fake_acp(prompt_timeout=True)
        cfg = _cfg([AcpAgentConfig(name="codex", command="codex", timeout_seconds=5)])
        (tool,) = acp_client.acp_agent_tools(cfg, str(tmp_path), "t1")
        out = asyncio.run(tool.ainvoke({"agent": "codex", "prompt": "长任务"}))
        assert "超时" in out and "codex" in out

    def test_sdk_missing_returns_install_hint(self, tmp_path, monkeypatch):
        import builtins

        cfg = _cfg([AcpAgentConfig(name="codex", command="codex")])
        (tool,) = acp_client.acp_agent_tools(cfg, str(tmp_path), "t1")
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "acp" or name.startswith("acp."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        out = asyncio.run(tool.ainvoke({"agent": "codex", "prompt": "x"}))
        assert "agent-client-protocol" in out and "未安装" in out

    def test_file_not_found_hint(self, tmp_path, monkeypatch):
        cfg = _cfg([AcpAgentConfig(name="ghost", command="ghost-agent")])
        (tool,) = acp_client.acp_agent_tools(cfg, str(tmp_path), "t1")
        _install_fake_acp()

        # 让 fake_spawn 抛 FileNotFoundError
        # 让 spawn 在 __aexit__ 阶段抛 FileNotFoundError（真实 SDK 是 @asynccontextmanager）
        class _Conn:
            async def initialize(self, **kw):
                pass

            async def new_session(self, **kw):
                return SimpleNamespace(session_id="s1")

            async def prompt(self, **kw):
                pass

        @asynccontextmanager
        async def failing_spawn(client, command, *args, env=None, cwd=None, **kw):
            yield (_Conn(), SimpleNamespace())
            raise FileNotFoundError(command)

        mod = sys.modules["acp"]
        mod.spawn_agent_process = failing_spawn
        out = asyncio.run(tool.ainvoke({"agent": "ghost", "prompt": "x"}))
        assert "不在 PATH" in out
        assert "ghost-agent" in out


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
class TestHelpers:
    def test_expand_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret")
        out = acp_client._expand_env({"A": "$MY_KEY", "B": "plain", "C": "$MISSING"})
        assert out == {"A": "secret", "B": "plain", "C": "$MISSING"}

    def test_permission_auto_approve(self):
        fake = _install_fake_acp()
        options = [
            SimpleNamespace(kind="allow_always", option_id="opt-always"),
            SimpleNamespace(kind="allow_once", option_id="opt-once"),
        ]
        resp = acp_client._build_permission_response(options, auto_approve=True)
        assert resp.outcome.outcome == "selected"
        assert resp.outcome.optionId == "opt-once"  # allow_once 优先

    def test_permission_deny_by_default(self):
        _install_fake_acp()
        resp = acp_client._build_permission_response([SimpleNamespace(kind="allow_once", option_id="x")], auto_approve=False)
        assert resp.outcome.outcome == "cancelled"


class TestBuildAgentAcp:
    def test_include_acp_flag(self, tmp_path, monkeypatch):
        """build_agent include_acp=False 时不组装 ACP 工具（monkeypatch 验证调用）。"""
        import src.open_llm_vtuber.task_platform.conf_bridge as cb
        from src.open_llm_vtuber.task_platform import graph

        ws = tmp_path / "ws"
        ws.mkdir()
        calls: list[str] = []

        def fake_acp_tools(cfg, workspace, task_id):
            calls.append(workspace)
            return []

        monkeypatch.setattr(graph, "acp_agent_tools", fake_acp_tools)
        cfg = cb.TaskPlatformConfig(
            db_path=str(tmp_path / "meta.db"),
            agents_root=str(tmp_path / "agents"),
            acp_agents=[AcpAgentConfig(name="codex", command="codex")],
        )
        stub = _StubModel()
        _write_agent(tmp_path / "agents", "explore", tools="read")

        async def run(include_acp):
            async with graph.build_agent(str(ws), "t1", cfg=cfg, model=stub,
                                         include_acp=include_acp) as agent:
                await agent.ainvoke(
                    {"messages": [AIMessage(content="done")], "goal": "g", "workspace": str(ws)},
                    {"configurable": {"thread_id": "t1"}},
                )

        asyncio.run(run(include_acp=False))
        assert calls == []  # include_acp=False → 不组装

        asyncio.run(run(include_acp=True))
        assert len(calls) == 1  # include_acp=True → 组装（即使 acp_agents 配置存在）


# --------------------------------------------------------------------------- #
# 脚手架（复用 test_task_agents 的 StubModel/agent 写入）
# --------------------------------------------------------------------------- #
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field


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


def _write_agent(root: Path, name: str, **fm):
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{name}.md"
    data = {"description": f"测试子 agent {name}"}
    data.update(fm)
    lines = []
    for k, v in data.items():
        vv = str(v).lower() if isinstance(v, bool) else v
        lines.append(f"{k}: {vv}")
    p.write_text(f"---\n" + "\n".join(lines) + f"\n---\n正文\n", encoding="utf-8")
    return p
