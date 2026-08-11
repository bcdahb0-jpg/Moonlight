"""v3 Phase 3：dangling tool_call 恢复测试。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_dangling.py -q
"""
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.open_llm_vtuber.task_platform.dangling import (
    DanglingToolCallMiddleware,
    _synthetic_tool_message,
)


def _ai(content: str = "", tool_calls=None, msg_id: str = "ai-1") -> AIMessage:
    return AIMessage(content=content, tool_calls=tool_calls or [], id=msg_id)


def _tc(name: str, args: dict, cid: str | None = None) -> dict:
    return {"name": name, "args": args, "id": cid}


def _tool_msg(cid: str, content: str = "ok") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=cid)


def _request(messages):
    return SimpleNamespace(messages=messages)


def _patch(messages):
    req = _request(list(messages))
    DanglingToolCallMiddleware._patch_messages(req)
    return req.messages


class TestOrphanToolCall:
    def test_complete_history_unchanged(self):
        msgs = [
            HumanMessage(content="hi"),
            _ai("think", [_tc("read_file", {"path": "a.txt"}, "c1")]),
            _tool_msg("c1"),
            _ai("done"),
        ]
        out = _patch(msgs)
        assert len(out) == len(msgs)
        assert isinstance(out[-1], AIMessage)

    def test_orphan_call_gets_synthetic_result(self):
        """AI 声明了 tool_call 但 ToolMessage 缺失 → 其后插入合成 error。"""
        msgs = [
            HumanMessage(content="hi"),
            _ai("think", [_tc("read_file", {"path": "a.txt"}, "c1")]),
            _ai("done"),  # 中断后 resume：结果丢了
        ]
        out = _patch(msgs)
        assert len(out) == 4
        synth = out[2]
        assert isinstance(synth, ToolMessage)
        assert synth.status == "error"
        assert synth.tool_call_id == "c1"
        assert "[执行恢复]" in str(synth.content)
        assert "read_file" in str(synth.content)
        # 后续消息不受影响
        assert isinstance(out[3], AIMessage)
        assert out[3].content == "done"

    def test_synthetic_message_not_duplicated_on_second_pass(self):
        """同一历史二次 patch（多次进模型）不重复插入。"""
        msgs = [
            HumanMessage(content="hi"),
            _ai("think", [_tc("read_file", {"path": "a.txt"}, "c1")]),
            _ai("done"),
        ]
        out1 = _patch(msgs)
        out2 = _patch(out1)
        assert len(out2) == len(out1) == 4

    def test_write_file_oversize_special_message(self):
        """write_file 超长参数 → 专项提示（拆分/str_replace），不盲目重试。"""
        tm = _synthetic_tool_message("c9", _tc("write_file", {"path": "x", "content": "x" * 1_100_000}, "c9"))
        assert "超长" in str(tm.content)
        assert "str_replace" in str(tm.content)

    def test_normal_write_file_message(self):
        tm = _synthetic_tool_message("c8", _tc("write_file", {"path": "x", "content": "small"}, "c8"))
        assert "缺失" in str(tm.content)
        assert "超长" not in str(tm.content)


class TestOrphanToolResult:
    def test_orphan_result_dropped(self):
        """ToolMessage 的来源调用已不在历史（压缩删除）→ 丢弃。"""
        msgs = [
            HumanMessage(content="hi"),
            _tool_msg("ghost-call"),  # 无声明
            _ai("done"),
        ]
        out = _patch(msgs)
        assert len(out) == 2
        assert all(not isinstance(m, ToolMessage) for m in out)

    def test_parallel_empty_ids_paired(self):
        """空 id tool_call 与后续 ToolMessage 按位置配对（不误判孤儿）。"""
        msgs = [
            HumanMessage(content="hi"),
            _ai("both", [_tc("read_file", {"path": "a"}, None), _tc("grep", {"pattern": "x"}, None)]),
            _tool_msg("r1", "first result"),
            _tool_msg("r2", "second result"),
            _ai("done"),
        ]
        # 空 id 调用：无法精确配对 → 都不算孤儿（结果存在，数量匹配）
        out = _patch(msgs)
        assert len(out) == len(msgs)  # 无插入无丢弃

    def test_mixed_orphan_and_ok(self):
        """并行调用中部分有结果、部分缺失 → 只补缺失的。"""
        msgs = [
            HumanMessage(content="hi"),
            _ai("both", [_tc("read_file", {"path": "a"}, "c1"), _tc("grep", {"pattern": "x"}, "c2")]),
            _tool_msg("c1"),  # c1 有结果
            # c2 缺失
            _ai("done"),
        ]
        out = _patch(msgs)
        assert len(out) == 5
        # 找到合成的 error ToolMessage（c2），c1 的结果消息保留
        synth = next(m for m in out if isinstance(m, ToolMessage) and m.status == "error")
        assert synth.tool_call_id == "c2"
        assert synth.status == "error"


class TestMiddlewareDispatch:
    def test_sync_path(self):
        mw = DanglingToolCallMiddleware()
        msgs = [HumanMessage(content="hi"), _ai("t", [_tc("read_file", {"path": "a"}, "c1")])]
        req = _request(msgs)
        captured = {}

        def handler(request):
            captured["msgs"] = request.messages
            return "model-response"

        result = mw.wrap_model_call(req, handler)
        assert result == "model-response"
        # handler 收到的是 patch 后的消息（含合成 ToolMessage）
        assert any(isinstance(m, ToolMessage) for m in captured["msgs"])

    def test_disabled_passthrough(self):
        mw = DanglingToolCallMiddleware(enabled=False)
        msgs = [HumanMessage(content="hi"), _ai("t", [_tc("read_file", {"path": "a"}, "c1")])]
        req = _request(msgs)

        def handler(request):
            return len(request.messages)

        assert mw.wrap_model_call(req, handler) == 2

    def test_async_path(self):
        import asyncio

        mw = DanglingToolCallMiddleware()
        msgs = [HumanMessage(content="hi"), _ai("t", [_tc("read_file", {"path": "a"}, "c1")])]
        req = _request(msgs)
        captured = {}

        async def handler(request):
            captured["msgs"] = request.messages
            return "async-response"

        async def run():
            return await mw.awrap_model_call(req, handler)

        result = asyncio.run(run())
        assert result == "async-response"
        assert any(isinstance(m, ToolMessage) for m in captured["msgs"])

    def test_empty_messages_noop(self):
        out = _patch([])
        assert out == []
