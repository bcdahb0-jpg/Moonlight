"""Phase 2a：白名单沙箱工具逃逸测试（TDD，plan §5.3 验收）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_sandbox.py -q
"""
import asyncio
import os
import tempfile

import pytest

from src.open_llm_vtuber.task_platform.sandbox import (
    Sandbox,
    SandboxPathError,
    sandbox_tools,
)


@pytest.fixture()
def ws(tmp_path):
    """临时 workspace：含一个子目录，供沙箱读写。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "hello.txt").write_text("hello sandbox", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def sb(ws):
    return Sandbox(str(ws), timeout_sec=10)


class TestResolveLocal:
    def test_accepts_relative_in_workspace(self, sb, ws):
        assert sb.resolve_local("sub") == os.path.realpath(str(ws / "sub"))
        assert sb.resolve_local("hello.txt") == os.path.realpath(str(ws / "hello.txt"))

    def test_accepts_absolute_in_workspace(self, sb, ws):
        assert sb.resolve_local(str(ws / "sub")) == os.path.realpath(str(ws / "sub"))

    def test_accepts_container_virtual_path(self, sb, ws):
        """`/workspace/...` 虚拟路径（to_container 输出形式）应反掩码回 workspace 内。"""
        assert sb.resolve_local("/workspace/hello.txt") == os.path.realpath(str(ws / "hello.txt"))
        assert sb.resolve_local("/workspace/sub") == os.path.realpath(str(ws / "sub"))
        assert sb.resolve_local("/workspace") == os.path.realpath(str(ws))
        assert sb.resolve_local("/workspace/") == os.path.realpath(str(ws))
        # 混合分隔符（模型可能输出 \workspace\...）
        assert sb.resolve_local("\\workspace\\hello.txt") == os.path.realpath(str(ws / "hello.txt"))

    def test_rejects_parent_escape(self, sb):
        # workspace 的父目录不在 workspace 内
        with pytest.raises(SandboxPathError):
            sb.resolve_local("../")
        with pytest.raises(SandboxPathError):
            sb.resolve_local("a/../../b")

    def test_rejects_absolute_escape(self, sb, ws):
        outside = tempfile.mkdtemp(prefix="sandbox-outside-")
        try:
            with pytest.raises(SandboxPathError):
                sb.resolve_local(outside)
        finally:
            os.rmdir(outside)

    def test_rejects_symlink_escape(self, sb, ws):
        """workspace 内 symlink 指向 workspace 外 → 拒绝。"""
        outside = tempfile.mkdtemp(prefix="sandbox-outside-")
        link = ws / "escape"
        try:
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as e:
                pytest.skip(f"无法创建 symlink（需管理员/开发者模式）：{e}")
            if not os.path.islink(link):
                pytest.skip("os.symlink 静默失败（Windows 无 SeCreateSymbolicLinkPrivilege）")
            with pytest.raises(SandboxPathError):
                sb.resolve_local(str(link))
        finally:
            if link.exists() or os.path.islink(link):
                os.unlink(link)
            os.rmdir(outside)

    def test_resolves_symlink_inside_workspace(self, sb, ws):
        """workspace 内 symlink 指向 workspace 内 → 放行。"""
        link = ws / "alias"
        try:
            try:
                os.symlink(ws / "sub", link, target_is_directory=True)
            except OSError as e:
                pytest.skip(f"无法创建 symlink：{e}")
            if not os.path.islink(link):
                pytest.skip("os.symlink 静默失败（Windows 无 SeCreateSymbolicLinkPrivilege）")
            assert sb.resolve_local(str(link)) == os.path.realpath(str(ws / "sub"))
        finally:
            if os.path.islink(link):
                os.unlink(link)


class TestFileTools:
    def test_write_read_roundtrip(self, sb, ws):
        out = sb.write_file("sub/out.txt", "数据 payload")
        assert "已写入" in out and "out.txt" in out
        assert sb.read_file("sub/out.txt") == "数据 payload"

    def test_write_read_roundtrip_with_container_path(self, sb, ws):
        """模型按 system prompt 用 /workspace/... 写读应一次成功（2026-08-10 修复）。"""
        out = sb.write_file("/workspace/sub/out2.txt", "虚拟路径 payload")
        assert "已写入" in out and "out2.txt" in out
        # 返回值中的掩码路径可再次作为入参（闭环）
        masked = sb.mapping.to_container(os.path.realpath(str(ws / "sub/out2.txt")))
        assert sb.read_file(masked) == "虚拟路径 payload"

    def test_glob_with_container_path(self, sb, ws):
        matches = sb.glob("/workspace/**/*.txt")
        assert "/workspace/hello.txt" in matches

    def test_ls_lists_entries(self, sb, ws):
        listing = sb.ls(".")
        assert "hello.txt" in listing
        assert "sub" in listing

    def test_escape_rejected_fail_soft(self, sb):
        # 工具层 fail-soft：不抛异常，返回 [沙箱] 提示
        assert "[沙箱]" in sb.read_file("../secret.txt")
        assert "[沙箱]" in sb.write_file("../../evil.txt", "x")
        assert "[沙箱]" in sb.ls("../../")

    def test_path_mapping_masks_host_path(self, sb, ws):
        host = str(ws / "hello.txt").replace("\\", "/")
        masked = sb.mapping.to_container(f"文件在 {host} 里")
        assert "/workspace/hello.txt" in masked
        assert host not in masked


class TestBash:
    def test_bash_runs_in_workspace_cwd(self, sb, ws):
        """bash 的 cwd 是 workspace：重定向写入的文件落在 workspace 内。"""
        result = asyncio.run(sb.bash("echo hi > out.txt"))
        assert "超时" not in result
        assert sb.read_file("out.txt").strip() == "hi"

    def test_bash_timeout_kills(self, sb):
        result = asyncio.run(sb.bash("ping -n 10 127.0.0.1 >nul"))
        # timeout_sec=10 由 fixture 传入，仅断言不会挂死
        assert isinstance(result, str)


class TestSandboxTools:
    def test_builds_seven_whitelisted_tools(self, ws):
        tools = sandbox_tools(str(ws))
        names = {getattr(t, "name", "") for t in tools}
        assert names == {"ls", "glob", "grep", "read_file", "write_file", "str_replace", "bash"}

    def test_write_tool_wrapped(self, ws):
        tools = {getattr(t, "name"): t for t in sandbox_tools(str(ws))}
        assert "[沙箱]" in tools["write_file"].invoke({"path": "../x.txt", "content": "y"})
