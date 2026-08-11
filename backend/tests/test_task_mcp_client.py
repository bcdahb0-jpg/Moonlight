"""Phase 2b：mcp_client 工具合并 + fail-soft 测试（TDD，plan §5.9 / §8）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_mcp_client.py -q --basetemp=./.pytest-tmp

覆盖（plan §8 Phase 2b 验收）：
- server_connection：McpServerConfig → adapter Connection 映射（stdio/http/缺字段）。
- load_tools：多服务器合并；**单服务器失败 fail-soft**（好服务器工具保留）。
- probe_servers：connected/error/disabled/misconfigured 状态机。
- build_agent：MCP 工具并入 agent tools，端到端可执行（注入假客户端，绕开真实子进程）。
"""
import asyncio
from typing import Any, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import graph, mcp_client
from src.open_llm_vtuber.task_platform.conf_bridge import McpServerConfig, TaskPlatformConfig


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


def _cfg(tmp_path, **servers) -> TaskPlatformConfig:
    """带 mcp_servers 的临时配置。servers: {name: McpServerConfig}。"""
    return TaskPlatformConfig(
        db_path=str(tmp_path / "meta.db"),
        mcp_servers=list(servers.values()),
    )


def _stdio(name: str, enabled: bool = True) -> McpServerConfig:
    return McpServerConfig(name=name, transport="stdio", command="uvx", args=["mcp-server-x"], enabled=enabled)


class _FakeMcpClient:
    """adapter 客户端替身：按服务器名返回工具或抛错。"""

    def __init__(self, tools_by_server: dict, fail: dict | None = None):
        self.tools_by_server = tools_by_server
        self.fail = fail or {}

    async def get_tools(self, server_name: str | None = None) -> list:
        if server_name in self.fail:
            raise RuntimeError(self.fail[server_name])
        return self.tools_by_server.get(server_name or "", [])


class _HangingMcpClient:
    """adapter 客户端替身：get_tools 永不返回（模拟子进程握手挂死）。"""

    async def get_tools(self, server_name: str | None = None) -> list:
        await asyncio.sleep(99)
        return []


def _make_factory(tools_by_server: dict, fail: dict | None = None):
    def factory(_cfg):
        return _FakeMcpClient(tools_by_server, fail=fail)

    return factory


@tool
def mcp_echo(x: str) -> str:
    """MCP 工具（记录调用）。"""
    return f"echo:{x}"


# --------------------------------------------------------------------------- #
# 1. server_connection 映射
# --------------------------------------------------------------------------- #
class TestServerConnection:
    def test_stdio_maps_to_command_args(self):
        s = _stdio("fetch")
        conn = mcp_client.server_connection(s)
        assert conn == {"transport": "stdio", "command": "uvx", "args": ["mcp-server-x"]}

    def test_http_maps_to_url_headers(self):
        s = McpServerConfig(
            name="remote", transport="http", url="http://127.0.0.1:9000/mcp",
            headers={"Authorization": "Bearer xyz"},
        )
        conn = mcp_client.server_connection(s)
        assert conn == {
            "transport": "http",
            "url": "http://127.0.0.1:9000/mcp",
            "headers": {"Authorization": "Bearer xyz"},
        }

    def test_disabled_returns_empty(self):
        assert mcp_client.server_connection(_stdio("off", enabled=False)) == {}

    def test_stdio_missing_command_returns_empty(self):
        s = McpServerConfig(name="bad", transport="stdio", command="", args=[])
        assert mcp_client.server_connection(s) == {}

    def test_unsupported_transport_returns_empty(self):
        s = McpServerConfig(name="weird", transport="carrier-pigeon", url="x")
        assert mcp_client.server_connection(s) == {}


# --------------------------------------------------------------------------- #
# 2. load_tools：合并 + fail-soft
# --------------------------------------------------------------------------- #
class TestLoadTools:
    def test_merges_all_servers(self, tmp_path):
        cfg = _cfg(
            tmp_path, a=_stdio("a"), b=_stdio("b"),
        )

        async def scenario():
            tools = await mcp_client.load_tools(
                cfg, client_factory=_make_factory({"a": [mcp_echo], "b": [mcp_echo]})
            )
            assert [t.name for t in tools] == ["mcp_echo", "mcp_echo"]

        asyncio.run(scenario())

    def test_fail_soft_keeps_good_servers(self, tmp_path):
        """坏服务器失败不影响好服务器工具（per-server 隔离）。"""
        cfg = _cfg(tmp_path, good=_stdio("good"), bad=_stdio("bad"))

        async def scenario():
            tools = await mcp_client.load_tools(
                cfg, client_factory=_make_factory({"good": [mcp_echo]}, fail={"bad": "boom"})
            )
            assert [t.name for t in tools] == ["mcp_echo"], "坏服务器应被跳过，好服务器保留"

        asyncio.run(scenario())

    def test_all_fail_still_returns_empty(self, tmp_path):
        """全部 MCP 失败也不阻塞：返回空工具列表（sandbox+skill 兜底）。"""
        cfg = _cfg(tmp_path, bad1=_stdio("bad1"), bad2=_stdio("bad2"))

        async def scenario():
            tools = await mcp_client.load_tools(
                cfg, client_factory=_make_factory({}, fail={"bad1": "x", "bad2": "y"})
            )
            assert tools == []

        asyncio.run(scenario())

    def test_no_servers_returns_empty(self, tmp_path):
        cfg = _cfg(tmp_path)

        async def scenario():
            assert await mcp_client.load_tools(cfg) == []

        asyncio.run(scenario())

    def test_timeout_is_fail_soft(self, tmp_path):
        """挂死的服务器触发 asyncio.wait_for 超时 → 返回空（不挂起、不抛）。"""
        cfg = _cfg(tmp_path, slow=_stdio("slow"))

        def factory(_cfg):
            return _HangingMcpClient()

        async def scenario():
            tools = await mcp_client.load_tools(cfg, client_factory=factory, timeout_sec=0.05)
            assert tools == []

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3. probe_servers 状态机
# --------------------------------------------------------------------------- #
class TestProbeServers:
    def test_statuses(self, tmp_path):
        cfg = _cfg(
            tmp_path,
            good=_stdio("good"),
            bad=_stdio("bad"),
            off=_stdio("off", enabled=False),
            mis=McpServerConfig(name="mis", transport="stdio", command="", args=[]),
        )

        async def scenario():
            statuses = await mcp_client.probe_servers(
                cfg, client_factory=_make_factory({"good": [mcp_echo]}, fail={"bad": "boom"})
            )
            by_name = {s["name"]: s for s in statuses}
            assert by_name["good"]["status"] == "connected"
            assert by_name["good"]["tool_count"] == 1
            assert by_name["good"]["tools"] == ["mcp_echo"]
            assert by_name["bad"]["status"] == "error"
            assert "boom" in by_name["bad"]["error"]
            assert by_name["off"]["status"] == "disabled"
            assert by_name["mis"]["status"] == "misconfigured"

        asyncio.run(scenario())

    def test_probe_timeout_is_error(self, tmp_path):
        """挂死服务器探测超时 → status=error（timeout 描述），不抛。"""
        cfg = _cfg(tmp_path, slow=_stdio("slow"))

        def factory(_cfg):
            return _HangingMcpClient()

        async def scenario():
            statuses = await mcp_client.probe_servers(cfg, client_factory=factory, timeout_sec=0.05)
            assert statuses[0]["status"] == "error"
            assert "timeout" in statuses[0]["error"]

        asyncio.run(scenario())

    def test_probe_factory_raise_is_fail_soft(self, tmp_path):
        """客户端构造抛错 → 启用服务器均记为 error（fail-soft 铁律：探测绝不抛）。"""
        cfg = _cfg(tmp_path, good=_stdio("good"))

        def bad_factory(_cfg):
            raise RuntimeError("factory boom")

        async def scenario():
            statuses = await mcp_client.probe_servers(cfg, client_factory=bad_factory)
            assert statuses[0]["status"] == "error"
            assert "factory boom" in statuses[0]["error"]

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3b. available_tools：GET /api/tasks/tools 委托
# --------------------------------------------------------------------------- #
class TestAvailableTools:
    def test_lists_sandbox_skill_mcp(self, tmp_path):
        cfg = TaskPlatformConfig(
            db_path=str(tmp_path / "meta.db"),
            tasks_root=str(tmp_path),
            skills_root=str(tmp_path / "no-skills"),  # 隔离：不读仓库真实技能库
            mcp_servers=[_stdio("good")],
        )

        async def scenario():
            tools = await graph.available_tools(
                cfg, mcp_client_factory=_make_factory({"good": [mcp_echo]})
            )
            assert set(tools["sandbox"]) == {"ls", "glob", "grep", "read_file", "write_file", "str_replace", "bash"}
            assert tools["skill"] == [], "空技能库 → skill 列表为空（Phase 3 填充见 test_task_skills）"
            by_name = {s["name"]: s for s in tools["mcp"]}
            assert by_name["good"]["status"] == "connected"
            assert by_name["good"]["tools"] == ["mcp_echo"]

        asyncio.run(scenario())

    def test_tasks_root_missing_is_ok(self, tmp_path):
        """tasks_root 目录不存在时 available_tools 不应抛错（Windows realpath 需目录存在）。"""
        cfg = TaskPlatformConfig(
            db_path=str(tmp_path / "meta.db"),
            tasks_root=str(tmp_path / "does-not-exist"),
            mcp_servers=[],
        )

        async def scenario():
            tools = await graph.available_tools(cfg)
            assert set(tools["sandbox"]) == {"ls", "glob", "grep", "read_file", "write_file", "str_replace", "bash"}

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 4. build_agent：MCP 工具并入 agent tools
# --------------------------------------------------------------------------- #
class TestBuildAgentMcp:
    def test_mcp_tools_in_agent_and_execute_end_to_end(self, tmp_path):
        mcp_calls: list = []

        @tool
        def mcp_echo(x: str) -> str:
            """MCP 工具（记录调用）。"""
            mcp_calls.append(x)
            return f"echo:{x}"

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = _cfg(tmp_path, good=_stdio("good"))
        model = _stub_model(
            AIMessage(content="", tool_calls=[_tc("mcp_echo", {"x": "hello"}, "tc1")]),
            AIMessage(content="done"),
        )

        async def scenario():
            async with graph.build_agent(
                str(ws), "t-mcp-1", cfg=cfg, model=model,
                mcp_client_factory=_make_factory({"good": [mcp_echo]}),
            ) as agent:
                tool_node = agent.nodes["tools"].bound
                names = set(tool_node.tools_by_name.keys())
                assert "mcp_echo" in names, f"MCP 工具应并入：{names}"
                assert "bash" in names, "sandbox 工具应保留"
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="mcp")], "goal": "g", "workspace": str(ws)},
                    {"configurable": {"thread_id": "t-mcp-1"}},
                )
            contents = [str(m.content) for m in state["messages"]]
            assert "done" in contents
            assert mcp_calls == ["hello"], f"MCP 工具应真实执行：{mcp_calls}"

        asyncio.run(scenario())
