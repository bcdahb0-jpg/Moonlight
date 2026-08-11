"""Phase 6c：extensions 钩子注册表测试（plan §8 6c）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_extensions.py -q --basetemp=./.pytest-tmp

覆盖（plan §8 6c）：
- ExtensionAPI：on/emit/has_event；未知事件抛错；handler 异常被吞（不炸 run）。
- PluginRegistry.scan：加载单文件插件 / 包插件；`.disabled` 后缀跳过；缺失根为空。
- apply_before_agent_start：最后一个非空返回值覆盖 system prompt。
- ExtensionHookMiddleware：before_tool_call block（不执行工具）/ 改 args；
  after_tool_call 改写返回；无 handler 透传。
- build_agent 集成：插件 before_tool_call 拦截 bash → error ToolMessage；after 改写结果。
- graph.list_plugins + /api/plugins 路由。
"""
import asyncio
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import graph
from src.open_llm_vtuber.task_platform.extensions import (
    EVENTS,
    ExtensionAPI,
    ExtensionHookMiddleware,
    PluginRegistry,
    apply_before_agent_start,
)


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


class _RecordingStub(_StubModel):
    """记录每次模型调用收到的消息（验证 system prompt 注入）。"""

    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen = list(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _tc(name: str, args: dict, cid: str) -> dict:
    return {"name": name, "id": cid, "args": args}


def _write_plugin(root: Path, name: str, source: str, *, disabled: bool = False) -> Path:
    """写一个插件模块到 root。disabled=True 时文件名带 .disabled 后缀。"""
    root.mkdir(parents=True, exist_ok=True)
    fname = f"{name}.py.disabled" if disabled else f"{name}.py"
    p = root / fname
    p.write_text(source, encoding="utf-8")
    return p


def _req(name: str = "write_file", args: dict | None = None, cid: str = "c1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {"path": "a.txt", "content": "x"}, "id": cid},
        tool=None,
        state={},
        runtime=None,
    )


# --------------------------------------------------------------------------- #
# 1. ExtensionAPI
# --------------------------------------------------------------------------- #
class TestExtensionAPI:
    def test_events_contract(self):
        assert EVENTS == ("before_agent_start", "before_tool_call", "after_tool_call")

    def test_on_unknown_event_raises(self):
        api = ExtensionAPI()
        with pytest.raises(ValueError):
            api.on("no_such_event", lambda: None)

    def test_emit_collects_non_none(self):
        api = ExtensionAPI()
        api.on("before_tool_call", lambda n, a, c: None)  # 无返回值 → 不收集
        api.on("before_tool_call", lambda n, a, c: {"block": True, "reason": "x"})
        out = api.emit("before_tool_call", "bash", {}, {})
        assert out == [{"block": True, "reason": "x"}]

    def test_handler_exception_swallowed(self):
        api = ExtensionAPI()

        def boom(*args):
            raise RuntimeError("plugin bug")

        api.on("before_agent_start", boom)
        api.on("before_agent_start", lambda p, c: p + "!")
        out = api.emit("before_agent_start", "sp", {})
        assert out == ["sp!"]  # 异常被吞，后续 handler 仍执行

    def test_has_event(self):
        api = ExtensionAPI()
        assert api.has_event("before_tool_call") is False
        api.on("before_tool_call", lambda *a: None)
        assert api.has_event("before_tool_call") is True
        assert api.has_event("before_agent_start") is False


# --------------------------------------------------------------------------- #
# 2. PluginRegistry.scan
# --------------------------------------------------------------------------- #
class TestPluginRegistry:
    def test_load_single_file_plugin(self, tmp_path):
        _write_plugin(tmp_path, "blk", 'def register(api):\n    api.on("before_tool_call", lambda n, a, c: None)\n')
        reg = PluginRegistry.scan_root(tmp_path)
        assert reg.loaded == ["blk.py"]
        assert reg.api.has_event("before_tool_call")

    def test_load_package_plugin(self, tmp_path):
        pkg = tmp_path / "hookpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            'def register(api):\n    api.on("after_tool_call", lambda n, r, c: None)\n',
            encoding="utf-8",
        )
        reg = PluginRegistry.scan_root(tmp_path)
        assert reg.loaded == ["hookpkg"]
        assert reg.api.has_event("after_tool_call")

    def test_disabled_suffix_skipped(self, tmp_path):
        _write_plugin(tmp_path, "old", 'def register(api):\n    pass\n', disabled=True)
        _write_plugin(tmp_path, "new", 'def register(api):\n    pass\n')
        reg = PluginRegistry.scan_root(tmp_path)
        assert reg.loaded == ["new.py"]

    def test_missing_root_empty(self, tmp_path):
        reg = PluginRegistry.scan_root(tmp_path / "nope")
        assert reg.loaded == []

    def test_plugin_without_register_skipped(self, tmp_path):
        _write_plugin(tmp_path, "bad", "x = 1\n")
        reg = PluginRegistry.scan_root(tmp_path)
        assert reg.loaded == []

    def test_broken_plugin_skipped(self, tmp_path):
        _write_plugin(tmp_path, "crashes", "raise RuntimeError('import boom')\n")
        reg = PluginRegistry.scan_root(tmp_path)
        assert reg.loaded == []

    def test_non_py_file_ignored(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
        reg = PluginRegistry.scan_root(tmp_path)
        assert reg.loaded == []


# --------------------------------------------------------------------------- #
# 3. apply_before_agent_start
# --------------------------------------------------------------------------- #
class TestBeforeAgentStart:
    def test_last_nonempty_wins(self):
        api = ExtensionAPI()
        api.on("before_agent_start", lambda p, c: p + " A")
        api.on("before_agent_start", lambda p, c: "B 完全覆盖")
        assert apply_before_agent_start(api, "base", {}) == "B 完全覆盖"

    def test_nonempty_skips_none(self):
        api = ExtensionAPI()
        api.on("before_agent_start", lambda p, c: None)
        api.on("before_agent_start", lambda p, c: "覆盖")
        assert apply_before_agent_start(api, "base", {}) == "覆盖"

    def test_no_handlers_passthrough(self):
        assert apply_before_agent_start(ExtensionAPI(), "base", {}) == "base"


# --------------------------------------------------------------------------- #
# 4. ExtensionHookMiddleware
# --------------------------------------------------------------------------- #
class TestExtensionHookMiddleware:
    def test_block_returns_error_tool_message(self):
        api = ExtensionAPI()
        api.on("before_tool_call", lambda n, a, c: {"block": True, "reason": "禁 bash"})
        mw = ExtensionHookMiddleware(_reg(api))
        called = {"n": 0}

        def execute(req):
            called["n"] += 1
            return ToolMessage(content="ok", status="success", tool_call_id="c1")

        out = mw.wrap_tool_call(_req("bash", {"cmd": "rm -rf"}, "c1"), execute)
        assert called["n"] == 0, "block 时工具不得执行"
        assert isinstance(out, ToolMessage)
        assert out.status == "error"
        assert "禁 bash" in out.content

    def test_before_rewrites_args(self):
        api = ExtensionAPI()
        api.on("before_tool_call", lambda n, a, c: {"args": {"path": "b.txt", "content": "y"}})
        mw = ExtensionHookMiddleware(_reg(api))
        seen = {}

        def execute(req):
            seen["args"] = req.tool_call["args"]
            return ToolMessage(content="ok", status="success", tool_call_id="c1")

        mw.wrap_tool_call(_req("write_file", {"path": "a.txt", "content": "x"}, "c1"), execute)
        assert seen["args"] == {"path": "b.txt", "content": "y"}

    def test_after_rewrites_result(self):
        api = ExtensionAPI()
        api.on("after_tool_call", lambda n, r, c: {"result": f"{r} [marked]"})
        mw = ExtensionHookMiddleware(_reg(api))
        out = mw.wrap_tool_call(
            _req("read_file"), lambda req: ToolMessage(content="abc", status="success", tool_call_id="c1")
        )
        assert "abc [marked]" in str(out.content)

    def test_no_handlers_passthrough(self):
        mw = ExtensionHookMiddleware(_reg(ExtensionAPI()))
        out = mw.wrap_tool_call(_req("write_file"), lambda req: "plain-result")
        assert out == "plain-result"

    def test_async_path(self):
        api = ExtensionAPI()
        api.on("before_tool_call", lambda n, a, c: {"block": True, "reason": "async block"})
        mw = ExtensionHookMiddleware(_reg(api))

        async def run():
            return await mw.awrap_tool_call(_req("bash", {"cmd": "x"}, "c1"), lambda req: "no-run")

        out = asyncio.run(run())
        assert out.status == "error"
        assert "async block" in out.content


def _reg(api: ExtensionAPI) -> PluginRegistry:
    reg = PluginRegistry(".")
    reg.api = api
    return reg


# --------------------------------------------------------------------------- #
# 5. build_agent 集成（插件真实介入工具链）
# --------------------------------------------------------------------------- #
def _plugin_env(tmp_path, monkeypatch, plugins_source: dict[str, str]):
    """搭临时插件根 + conf_bridge._cached 指向它，返回 plugins_root。"""
    import src.open_llm_vtuber.task_platform.conf_bridge as cb

    pdir = tmp_path / "plugins"
    for name, src in plugins_source.items():
        _write_plugin(pdir, name, src)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(
        cb, "_cached", cb.TaskPlatformConfig(
            db_path=str(tmp_path / "meta.db"), plugins_root=str(pdir), agents_root=str(tmp_path / "agents")
        )
    )
    return {"ws": ws, "pdir": pdir}


class TestBuildAgentExtensions:
    def test_plugin_blocks_bash(self, tmp_path, monkeypatch):
        env = _plugin_env(tmp_path, monkeypatch, {
            "blk": 'def register(api):\n'
                   '    api.on("before_tool_call", lambda n, a, c: {"block": True, "reason": "插件禁 bash"} if n == "bash" else None)\n'
        })
        model = _StubModel(
            responses=[
                AIMessage(content="", tool_calls=[_tc("bash", {"cmd": "echo hi"}, "tc1")]),
                AIMessage(content="完成"),
            ]
        )

        async def run():
            async with graph.build_agent(str(env["ws"]), "t1", cfg=cb_cfg(), model=model) as agent:
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="跑 bash")], "goal": "g", "workspace": str(env["ws"])},
                    {"configurable": {"thread_id": "t-ext-1"}},
                )
            return state

        state = asyncio.run(run())
        texts = [str(m.content) for m in state["messages"]]
        assert any("被扩展拦截" in t for t in texts), texts
        assert any("插件禁 bash" in t for t in texts), texts

    def test_plugin_after_rewrites_result(self, tmp_path, monkeypatch):
        env = _plugin_env(tmp_path, monkeypatch, {
            "mark": 'def register(api):\n'
                    '    api.on("after_tool_call", lambda n, r, c: {"result": r + " [marked]"} if n == "write_file" else None)\n'
        })
        model = _StubModel(
            responses=[
                AIMessage(content="", tool_calls=[_tc("write_file", {"path": "o.txt", "content": "hi"}, "tc1")]),
                AIMessage(content="done"),
            ]
        )

        async def run():
            async with graph.build_agent(str(env["ws"]), "t2", cfg=cb_cfg(), model=model) as agent:
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="写文件")], "goal": "g", "workspace": str(env["ws"])},
                    {"configurable": {"thread_id": "t-ext-2"}},
                )
            return state

        state = asyncio.run(run())
        texts = [str(m.content) for m in state["messages"]]
        assert any("[marked]" in t for t in texts), texts
        assert (env["ws"] / "o.txt").read_text(encoding="utf-8") == "hi"  # 工具真实执行

    def test_plugin_before_agent_start_rewrites_prompt(self, tmp_path, monkeypatch):
        env = _plugin_env(tmp_path, monkeypatch, {
            "pnote": 'def register(api):\n'
                     '    api.on("before_agent_start", lambda p, c: p + "【插件注入】只读模式")\n'
        })
        model = _RecordingStub(responses=[AIMessage(content="ok")])

        async def run():
            async with graph.build_agent(str(env["ws"]), "t3", cfg=cb_cfg(), model=model) as agent:
                await agent.ainvoke(
                    {"messages": [HumanMessage(content="hi")], "goal": "g", "workspace": str(env["ws"])},
                    {"configurable": {"thread_id": "t-ext-3"}},
                )

        asyncio.run(run())
        # system prompt 被插件改写 → 模型实际收到的 SystemMessage 含注入标记
        assert any("插件注入" in str(m.content) for m in model.seen), model.seen


def cb_cfg():
    import src.open_llm_vtuber.task_platform.conf_bridge as cb

    return cb.task_config()


# --------------------------------------------------------------------------- #
# 6. list_plugins + 路由
# --------------------------------------------------------------------------- #
class TestListPlugins:
    def test_list_plugins(self, tmp_path):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        _write_plugin(tmp_path, "blk", 'def register(api):\n    api.on("before_tool_call", lambda *a: None)\n')
        _write_plugin(tmp_path, "old", 'def register(api):\n    pass\n', disabled=True)
        cfg = cb.TaskPlatformConfig(plugins_root=str(tmp_path))
        plugins = graph.list_plugins(cfg)
        assert plugins == [{"name": "blk.py", "enabled": True}]


class TestPluginsRoute:
    def test_list_plugins_route(self, tmp_path, monkeypatch):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb
        import src.open_llm_vtuber.task_platform.task_route as task_route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        _write_plugin(tmp_path, "blk", 'def register(api):\n    api.on("before_tool_call", lambda *a: None)\n')
        monkeypatch.setattr(cb, "_cached", cb.TaskPlatformConfig(
            db_path=str(tmp_path / "meta.db"), plugins_root=str(tmp_path)))
        monkeypatch.setattr(task_route, "_is_local_request", lambda request: True)
        app = FastAPI()
        app.include_router(task_route.init_task_route())
        r = TestClient(app).get("/api/plugins")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["plugins"] == [{"name": "blk.py", "enabled": True}]

    def test_plugins_route_forbidden_outside_local(self, tmp_path, monkeypatch):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb
        import src.open_llm_vtuber.task_platform.task_route as task_route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setattr(cb, "_cached", cb.TaskPlatformConfig(db_path=str(tmp_path / "meta.db")))
        monkeypatch.setattr(task_route, "_is_local_request", lambda request: False)
        app = FastAPI()
        app.include_router(task_route.init_task_route())
        r = TestClient(app).get("/api/plugins")
        assert r.status_code == 403
