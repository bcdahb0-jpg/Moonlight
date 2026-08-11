"""v4 Phase B2：委派账本测试（从历史提取 delegate 调用、渲染注入、截断）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_ledger.py -q --basetemp=.pytest-tmp
"""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.open_llm_vtuber.task_platform.delegation_ledger import (
    DelegationLedgerMiddleware,
    bound_text,
    extract_delegations,
    render_ledger,
)


def _ai(tool_calls=None, content="think"):
    return AIMessage(content=content, tool_calls=tool_calls or [])


def _tc(name, args, cid):
    return {"name": name, "args": args, "id": cid}


def _tool(content, cid, status="success"):
    return ToolMessage(content=content, tool_call_id=cid, status=status)


class TestBoundText:
    def test_short_passthrough(self):
        assert bound_text("abc") == "abc"

    def test_long_head_tail(self):
        out = bound_text("a" * 100 + "B" * 10 + "c" * 100, cap=50)
        assert len(out) <= 50
        assert "..." in out
        assert out.startswith("a")
        assert out.endswith("c")


class TestExtractDelegations:
    def test_in_progress_only(self):
        msgs = [
            HumanMessage(content="go"),
            _ai([_tc("delegate", {"agent_name": "web-research", "task": "查 FastAPI 最新版"}, "c1")]),
        ]
        entries = extract_delegations(msgs)
        assert len(entries) == 1
        assert entries[0]["status"] == "in_progress"
        assert entries[0]["agent"] == "web-research"
        assert "FastAPI" in entries[0]["description"]

    def test_done_with_brief(self):
        msgs = [
            HumanMessage(content="go"),
            _ai([_tc("delegate", {"agent_name": "code-reviewer", "task": "审查 x.py"}, "c1")]),
            _tool("v2.3.4 发布于 2026-08-01，更新日志摘要……" * 100, "c1"),  # ~2400 字符 > 2000
        ]
        entries = extract_delegations(msgs)
        assert entries[0]["status"] == "done"
        assert "..." in entries[0]["result_brief"]  # 超 2000 截断（head+marker+tail）
        assert len(entries[0]["result_brief"]) <= 2000

    def test_error_status(self):
        msgs = [
            _ai([_tc("delegate", {"agent_name": "x", "task": "t"}, "c1")]),
            _tool("失败", "c1", status="error"),
        ]
        entries = extract_delegations(msgs)
        assert entries[0]["status"] == "error"

    def test_parallel_tool_tracked(self):
        msgs = [
            _ai([_tc("delegate_parallel", {"delegations": [{"agent_name": "a", "task": "t1"}, {"agent_name": "b", "task": "t2"}]}, "c1")]),
            _tool("[1] a: done\n[2] b: done", "c1"),
        ]
        entries = extract_delegations(msgs)
        assert len(entries) == 1
        assert entries[0]["status"] == "done"

    def test_non_delegate_tools_ignored(self):
        msgs = [_ai([_tc("read_file", {"path": "a"}, "c1")]), _tool("content", "c1")]
        assert extract_delegations(msgs) == []


class TestRenderLedger:
    def test_empty(self):
        assert render_ledger([]) == ""

    def test_newest_first(self):
        entries = [
            {"call_id": "c1", "agent": "a", "description": "任务1", "status": "done", "result_brief": "结果1"},
            {"call_id": "c2", "agent": "b", "description": "任务2", "status": "in_progress", "result_brief": ""},
        ]
        text = render_ledger(entries)
        assert text.startswith("## 已委派工作")
        assert text.index("任务2") < text.index("任务1")  # 新的在前
        assert "in_progress" in text

    def test_max_entries(self):
        entries = [
            {"call_id": f"c{i}", "agent": "a", "description": f"任务{i}", "status": "done", "result_brief": ""}
            for i in range(10)
        ]
        text = render_ledger(entries, max_entries=3)
        assert text.count("- [done]") == 3
        assert "超出预算" in text or "未列出" in text


class TestMiddleware:
    def _request(self, messages):
        return SimpleNamespace(messages=list(messages))

    def test_injects_ledger(self):
        mw = DelegationLedgerMiddleware()
        msgs = [
            HumanMessage(content="go"),
            _ai([_tc("delegate", {"agent_name": "web", "task": "搜索"}, "c1")]),
            _tool("结果", "c1"),
        ]
        req = self._request(msgs)
        mw.wrap_model_call(req, lambda r: "ok")
        assert req.messages[0].name == "delegation_ledger"
        assert "web" in req.messages[0].content
        assert len(req.messages) == len(msgs) + 1

    def test_no_delegation_no_inject(self):
        mw = DelegationLedgerMiddleware()
        msgs = [HumanMessage(content="hi"), _ai()]
        req = self._request(msgs)
        mw.wrap_model_call(req, lambda r: "ok")
        assert req.messages is not None
        assert not any(getattr(m, "name", "") == "delegation_ledger" for m in req.messages)

    def test_async_path(self):
        import asyncio

        mw = DelegationLedgerMiddleware()
        msgs = [
            HumanMessage(content="go"),
            _ai([_tc("delegate", {"agent_name": "a", "task": "t"}, "c1")]),
        ]
        req = self._request(msgs)

        async def handler(r):
            return "async-ok"

        async def run():
            return await mw.awrap_model_call(req, handler)

        assert asyncio.run(run()) == "async-ok"
        assert req.messages[0].name == "delegation_ledger"

    def test_disabled_noop(self):
        mw = DelegationLedgerMiddleware(enabled=False)
        msgs = [HumanMessage(content="go"), _ai([_tc("delegate", {"agent_name": "a", "task": "t"}, "c1")])]
        req = self._request(msgs)
        mw.wrap_model_call(req, lambda r: "ok")
        assert not any(getattr(m, "name", "") == "delegation_ledger" for m in req.messages)
