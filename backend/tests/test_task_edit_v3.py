"""v3 Phase 1：str_replace 精确编辑 + read_before_write 版本门测试。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_edit_v3.py -q
"""
import asyncio
import os

import pytest

from src.open_llm_vtuber.task_platform.read_before_write import (
    ReadBeforeWriteMiddleware,
    read_mark_store,
)
from src.open_llm_vtuber.task_platform.sandbox import Sandbox, sandbox_tools


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "hello.txt").write_text("hello sandbox\nline two\n", encoding="utf-8")
    (tmp_path / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    return tmp_path


@pytest.fixture()
def sb(ws):
    return Sandbox(str(ws), timeout_sec=10)


class TestStrReplace:
    def test_replace_first_occurrence(self, sb, ws):
        r = sb.str_replace("hello.txt", "hello", "HELLO")
        assert "已替换 1 处" in r
        assert (ws / "hello.txt").read_text(encoding="utf-8") == "HELLO sandbox\nline two\n"

    def test_replace_all(self, sb, ws):
        (ws / "multi.txt").write_text("x=1\ny=2\nx=3\n", encoding="utf-8")
        r = sb.str_replace("multi.txt", "x=", "z=", replace_all=True)
        assert "已替换 2 处" in r
        assert (ws / "multi.txt").read_text(encoding="utf-8") == "z=1\ny=2\nz=3\n"

    def test_not_found_returns_error_text(self, sb, ws):
        r = sb.str_replace("hello.txt", "不存在的内容", "x")
        assert "未找到要替换的文本" in r
        assert (ws / "hello.txt").read_text(encoding="utf-8")  # 未破坏文件

    def test_empty_old_str_rejected(self, sb):
        r = sb.str_replace("hello.txt", "", "x")
        assert "old_str 不能为空" in r

    def test_missing_file(self, sb):
        r = sb.str_replace("nope.txt", "a", "b")
        assert "不是文件" in r

    def test_preserves_crlf(self, sb, ws):
        r = sb.str_replace("crlf.txt", "a", "A")
        assert "已替换" in r
        assert (ws / "crlf.txt").read_bytes() == b"A\r\nb\r\n"

    def test_path_escape_rejected(self, sb):
        r = sb.str_replace("../outside.txt", "a", "b")
        assert "越界" in r or "路径" in r


class TestReadBeforeWrite:
    @pytest.fixture(autouse=True)
    def clean_store(self):
        read_mark_store().clear()
        yield
        read_mark_store().clear()

    def _tool_request(self, name: str, args: dict):
        """构造最小 ToolCallRequest（langchain middleware 类型不强制，用 dict 鸭子类型）。"""
        from types import SimpleNamespace

        return SimpleNamespace(
            tool_call={"name": name, "args": args, "id": "call_1"},
            tool=SimpleNamespace(name=name),
        )

    def _execute_ok(self, request):
        """直接调用沙箱方法模拟工具执行（返回字符串结果）。"""
        sb = self._sb
        name = request.tool_call["name"]
        args = request.tool_call["args"]
        if name == "read_file":
            return sb.read_file(args["path"])
        if name == "write_file":
            return sb.write_file(args["path"], args["content"])
        if name == "str_replace":
            return sb.str_replace(args["path"], args["old_str"], args["new_str"])
        raise AssertionError(f"unexpected tool {name}")

    @pytest.fixture()
    def mw(self, ws):
        self._sb = Sandbox(str(ws), timeout_sec=10)
        return ReadBeforeWriteMiddleware(str(ws), enabled=True)

    def test_write_without_read_allowed_first_time(self, mw, ws):
        """无 mark（首次写）→ fail-open 放行。"""
        req = self._tool_request("write_file", {"path": "hello.txt", "content": "new"})
        result = mw.wrap_tool_call(req, self._execute_ok)
        from langchain_core.messages import ToolMessage

        assert not (isinstance(result, ToolMessage) and result.status == "error")

    def test_read_then_write_allowed(self, mw, ws):
        """读过后写 → 放行。"""
        read_req = self._tool_request("read_file", {"path": "hello.txt"})
        mw.wrap_tool_call(read_req, self._execute_ok)
        write_req = self._tool_request("write_file", {"path": "hello.txt", "content": "edited"})
        result = mw.wrap_tool_call(write_req, self._execute_ok)
        assert "已写入" in str(result)

    def test_external_modify_blocks_write(self, mw, ws):
        """读后外部改动 → 写被阻断（error ToolMessage）。"""
        read_req = self._tool_request("read_file", {"path": "hello.txt"})
        mw.wrap_tool_call(read_req, self._execute_ok)
        # 外部改动文件（模拟另一进程/工具改了它）
        (ws / "hello.txt").write_text("external change\n", encoding="utf-8")
        write_req = self._tool_request("write_file", {"path": "hello.txt", "content": "mine"})
        result = mw.wrap_tool_call(write_req, self._execute_ok)
        from langchain_core.messages import ToolMessage

        assert isinstance(result, ToolMessage) and result.status == "error"
        assert "写前校验未通过" in str(result.content)
        # 文件未被覆盖
        assert (ws / "hello.txt").read_text(encoding="utf-8") == "external change\n"

    def test_container_virtual_path_gate_closed(self, mw, ws):
        """/workspace/... 虚拟路径：read 落 mark → 外部修改 → write 被阻断（反掩码闭环）。"""
        read_req = self._tool_request("read_file", {"path": "/workspace/hello.txt"})
        mw.wrap_tool_call(read_req, self._execute_ok)
        (ws / "hello.txt").write_text("external change\n", encoding="utf-8")
        write_req = self._tool_request("write_file", {"path": "/workspace/hello.txt", "content": "mine"})
        result = mw.wrap_tool_call(write_req, self._execute_ok)
        from langchain_core.messages import ToolMessage

        assert isinstance(result, ToolMessage) and result.status == "error"
        assert "写前校验未通过" in str(result.content)

    def test_async_path_parity(self, mw, ws):
        """异步路径与同步行为一致。"""

        async def run():
            read_req = self._tool_request("read_file", {"path": "hello.txt"})
            await mw.awrap_tool_call(read_req, self._async_exec)
            (ws / "hello.txt").write_text("changed outside\n", encoding="utf-8")
            write_req = self._tool_request("write_file", {"path": "hello.txt", "content": "x"})
            return await mw.awrap_tool_call(write_req, self._async_exec)

        result = asyncio.run(run())
        from langchain_core.messages import ToolMessage

        assert isinstance(result, ToolMessage) and result.status == "error"

    async def _async_exec(self, request):
        return self._execute_ok(request)

    def test_str_replace_gate_too(self, mw, ws):
        """str_replace 同样过版本门。"""
        read_req = self._tool_request("read_file", {"path": "hello.txt"})
        mw.wrap_tool_call(read_req, self._execute_ok)
        (ws / "hello.txt").write_text("outsider\n", encoding="utf-8")
        rep_req = self._tool_request("str_replace", {"path": "hello.txt", "old_str": "x", "new_str": "y"})
        result = mw.wrap_tool_call(rep_req, self._execute_ok)
        from langchain_core.messages import ToolMessage

        assert isinstance(result, ToolMessage) and result.status == "error"

    def test_new_file_write_not_checked(self, mw, ws):
        """新建文件（不存在）→ 不校验直接放行。"""
        req = self._tool_request("write_file", {"path": "new.txt", "content": "fresh"})
        result = mw.wrap_tool_call(req, self._execute_ok)
        assert "已写入" in str(result)

    def test_disabled_passes_through(self, ws):
        self._sb = Sandbox(str(ws), timeout_sec=10)
        mw = ReadBeforeWriteMiddleware(str(ws), enabled=False)
        req = self._tool_request("write_file", {"path": "hello.txt", "content": "x"})
        result = mw.wrap_tool_call(req, self._execute_ok)
        assert "已写入" in str(result)


class TestSandboxToolsRegistered:
    def test_str_replace_in_toolset(self, ws):
        names = [t.name for t in sandbox_tools(str(ws))]
        assert "str_replace" in names
        assert "read_file" in names
