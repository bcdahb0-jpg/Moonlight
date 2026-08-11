"""v4 Phase B1：ToolOutputBudget 测试（工具输出 >12K 落盘换预览 / 截断兜底）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_output_budget.py -q
"""
import re
import tempfile
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from src.open_llm_vtuber.task_platform.output_budget import (
    ToolOutputBudgetMiddleware,
    _snap_head,
    _snap_tail,
)


def _msg(content: str, name: str = "bash", call_id: str = "call_1") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=call_id)


def _mw(workspace: str, **kw) -> ToolOutputBudgetMiddleware:
    return ToolOutputBudgetMiddleware(workspace, "task-1", **kw)


class TestSmallOutputUnchanged:
    def test_below_threshold_passthrough(self, tmp_path):
        mw = _mw(str(tmp_path), externalize_min_chars=12000)
        msg = _msg("x" * 100)  # 远小于阈值
        out = mw._maybe_budget_result(msg)
        assert out is msg  # 同一对象，零改动

    def test_disabled_noop(self, tmp_path):
        mw = _mw(str(tmp_path), enabled=False, externalize_min_chars=1)
        msg = _msg("x" * 1000)
        out = mw._maybe_budget_result(msg)
        assert out is msg


class TestExternalize:
    def test_oversize_written_to_disk_and_replaced(self, tmp_path):
        mw = _mw(str(tmp_path), externalize_min_chars=10, preview_head_chars=20, preview_tail_chars=10)
        body = "line1\nline2\n" + "z" * 500 + "\nlast line"
        out = mw._maybe_budget_result(_msg(body, name="bash", call_id="call_9"))
        assert isinstance(out, ToolMessage)
        content = out.content
        # 替换为 preview：含虚拟路径 + 节选
        assert "/workspace/.pi/tasks/task-1/outputs/.tool-results/bash-" in content
        assert "line1" in content
        assert "last line" in content
        assert len(content) < len(body)
        # 落盘文件真实存在且内容完整
        m = re.search(r"(/workspace/.+\.log)", content)
        assert m
        real = Path(tmp_path) / m.group(1).removeprefix("/workspace/")
        assert real.is_file()
        assert real.read_text(encoding="utf-8") == body

    def test_tool_call_id_in_filename(self, tmp_path):
        mw = _mw(str(tmp_path), externalize_min_chars=1, preview_head_chars=10, preview_tail_chars=5)
        out = mw._maybe_budget_result(_msg("y" * 100, name="web_fetch"))
        content = out.content
        m = re.search(r"(/workspace/.+\.log)", content)
        assert m and "web_fetch-" in m.group(1)


class TestFallback:
    def test_disk_unavailable_falls_back_truncation(self, tmp_path):
        # workspace 指向一个"文件"（不是目录）→ mkdir 抛 OSError → 落盘失败 → 截断
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("occupied", encoding="utf-8")
        mw = _mw(str(blocker), externalize_min_chars=10, fallback_max_chars=200,
                 preview_head_chars=50, preview_tail_chars=30)
        body = "a" * 1000
        out = mw._maybe_budget_result(_msg(body))
        content = out.content
        assert len(content) <= 200
        assert "省略" in content  # 截断标记
        assert content.count("a") < 1000

    def test_fallback_respects_hard_cap(self, tmp_path):
        blocker = tmp_path / "b.txt"
        blocker.write_text("x", encoding="utf-8")
        mw = _mw(str(blocker), externalize_min_chars=1, fallback_max_chars=1000,
                 preview_head_chars=400, preview_tail_chars=200)
        out = mw._maybe_budget_result(_msg("b" * 50000))
        assert len(out.content) <= 1000


class TestExemptAndShape:
    def test_read_file_exempt(self, tmp_path):
        mw = _mw(str(tmp_path), externalize_min_chars=10)
        msg = _msg("r" * 5000, name="read_file")
        out = mw._maybe_budget_result(msg)
        assert out is msg  # 豁免：不落盘不截断（防 persist→read 循环）

    def test_multimodal_content_skipped(self, tmp_path):
        mw = _mw(str(tmp_path), externalize_min_chars=1)
        msg = ToolMessage(content=[{"type": "image", "source": "data"}], tool_call_id="c1")
        out = mw._maybe_budget_result(msg)
        assert out is msg

    def test_command_update_messages_patched(self, tmp_path):
        from dataclasses import dataclass, field

        @dataclass
        class _FakeCommand:
            update: dict = field(default_factory=dict)

        mw = _mw(str(tmp_path), externalize_min_chars=5)
        big = _msg("x" * 500)
        cmd = _FakeCommand(update={"messages": [_msg("ok"), big]})
        out = mw._maybe_budget_result(cmd)
        # 第二条第被替换，第一条原样
        assert out.update["messages"][0].content == "ok"
        assert "/workspace/.pi/tasks/task-1/outputs/.tool-results/" in out.update["messages"][1].content


class TestHistoricalPatch:
    def test_history_oversize_truncated_before_model(self, tmp_path):
        from types import SimpleNamespace

        mw = _mw(str(tmp_path), externalize_min_chars=10, fallback_max_chars=100,
                 preview_head_chars=30, preview_tail_chars=20)
        big = _msg("h" * 5000, name="bash")
        request = SimpleNamespace(messages=[_msg("small"), big, _msg("ok2")])
        mw._patch_historical(request)
        texts = [m.content for m in request.messages]
        assert texts[0] == "small"
        assert len(texts[1]) <= 100
        assert "省略" in texts[1]
        assert texts[2] == "ok2"

    def test_history_within_cap_untouched(self, tmp_path):
        from types import SimpleNamespace

        mw = _mw(str(tmp_path), fallback_max_chars=100)
        msgs = [_msg("ok", name="bash")]
        request = SimpleNamespace(messages=msgs)
        mw._patch_historical(request)
        assert request.messages is msgs  # 零改动（无重建）


class TestSnapHelpers:
    def test_snap_head_line_boundary(self):
        text = "line1\nline2\nline3\nline4"
        assert _snap_head(text, 11) == "line1\n"  # 预算内最后换行（index 5）处截断
        assert _snap_head(text, 5) == "line1"  # 预算内无换行 → 直接截

    def test_snap_tail_line_boundary(self):
        text = "line1\nline2\nline3\nline4"
        assert _snap_tail(text, 11) == "line4"  # 从预算内最近换行开始
        assert _snap_tail(text, 4) == "ine4"  # 无换行 → 直接取尾部 4 字符


def test_wrap_tool_call_hooks_roundtrip(tmp_path):
    """wrap_tool_call / awrap_tool_call 双实现均可路由到预算逻辑。"""
    import asyncio

    mw = _mw(str(tmp_path), externalize_min_chars=5)
    body = "x" * 300

    def execute(req):
        return _msg(body)

    async def aexecute(req):
        return _msg(body)

    sync_out = mw.wrap_tool_call(None, execute)
    assert "/workspace/" in sync_out.content
    async_out = asyncio.run(mw.awrap_tool_call(None, aexecute))
    assert "/workspace/" in async_out.content
