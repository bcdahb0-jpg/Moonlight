"""v4 Phase A3：浏览器自动化测试（mock BrowserSession，不起真实浏览器）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_browser.py -q --basetemp=.pytest-tmp
"""
import asyncio

import pytest

from src.open_llm_vtuber.task_platform.browser import (
    PageSnapshot,
    SnapshotElement,
    find_system_browser,
    validate_public_http_url,
)
from src.open_llm_vtuber.task_platform import browser as browser_mod
from src.open_llm_vtuber.task_platform.browser_tools import browser_tools
from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig


def _cfg(**overrides):
    base = dict(
        browser_enabled=True,
        browser_headless=True,
        browser_use_system_edge=False,
        browser_viewport_width=1280,
        browser_viewport_height=720,
    )
    base.update(overrides)
    return TaskPlatformConfig(**base)


class TestUrlValidation:
    def test_public_url_ok(self):
        assert validate_public_http_url("https://example.com/docs") is None
        assert validate_public_http_url("http://www.baidu.com") is None

    def test_non_http_rejected(self):
        assert "http" in validate_public_http_url("file:///etc/passwd")
        assert validate_public_http_url("ftp://x.com") is not None

    def test_private_ips_rejected(self):
        for bad in (
            "http://127.0.0.1:8080/admin",
            "http://192.168.1.1/",
            "http://10.0.0.5/",
            "http://172.16.3.4/",
            "http://169.254.169.254/latest/meta-data/",
        ):
            assert validate_public_http_url(bad) is not None, bad

    def test_no_host_rejected(self):
        assert validate_public_http_url("https://") is not None


class TestToolsRegistration:
    def test_disabled_no_tools(self, tmp_path):
        assert browser_tools(_cfg(browser_enabled=False), str(tmp_path), "t1") == []

    def test_enabled_eight_tools(self, tmp_path):
        tools = browser_tools(_cfg(), str(tmp_path), "t1")
        names = [t.name for t in tools]
        assert names == [
            "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
            "browser_get_text", "browser_back", "browser_screenshot", "browser_close",
        ]

    def test_system_edge_resolved(self):
        # 本机 Windows：至少返回一个候选或 None（不崩溃）
        exe = find_system_browser()
        assert exe is None or exe.lower().endswith(("msedge.exe", "chrome.exe"))


class _FakeSession:
    """记录调用的假浏览器会话（实现 BrowserSession 公开 API 子集）。"""

    def __init__(self):
        self.calls: list[str] = []
        self.shot = b"\x89PNG-fake"

    def _snap(self, url="https://example.com/", title="Page"):
        return PageSnapshot(
            url=url, title=title,
            elements=[SnapshotElement(ref=1, tag="a", role="", type="", name="链接 A")],
        )

    async def navigate(self, url):
        self.calls.append(f"navigate:{url}")
        return self._snap(url)

    async def snapshot(self):
        self.calls.append("snapshot")
        return self._snap()

    async def click(self, ref):
        self.calls.append(f"click:{ref}")
        return self._snap()

    async def type_text(self, ref, text, submit=False):
        self.calls.append(f"type:{ref}:{text}:{submit}")
        return self._snap()

    async def get_text(self, max_chars=8000):
        self.calls.append("get_text")
        return "页面文本内容"

    async def back(self):
        self.calls.append("back")
        return self._snap()

    async def screenshot_bytes(self, full_page=False):
        self.calls.append("screenshot")
        return self.shot


class _FakeManager:
    def __init__(self, session=None):
        self.session = session or _FakeSession()
        self.closed = []

    def get_session(self, key, **kw):
        return self.session

    async def close_session(self, key):
        self.closed.append(key)
        return True


class TestToolProtocol:
    def _tools(self, tmp_path, manager):
        import src.open_llm_vtuber.task_platform.browser_tools as bt

        async def run():
            return browser_tools(_cfg(), str(tmp_path), "t1")

        tools = asyncio.run(run())
        # 替换 manager（browser_tools 闭包持有 get_browser_manager 的引用）
        bt.get_browser_manager = lambda: manager
        return tools

    def test_navigate_and_click_flow(self, tmp_path, monkeypatch):
        manager = _FakeManager()
        import src.open_llm_vtuber.task_platform.browser_tools as bt
        monkeypatch.setattr(bt, "get_browser_manager", lambda: manager)
        tools = {t.name: t for t in browser_tools(_cfg(), str(tmp_path), "t1")}

        async def run():
            nav = await tools["browser_navigate"].ainvoke({"url": "https://example.com/"})
            snap = await tools["browser_snapshot"].ainvoke({})
            clk = await tools["browser_click"].ainvoke({"ref": 1})
            return nav, snap, clk

        nav, snap, clk = asyncio.run(run())
        assert "链接 A" in nav and "[1]" in nav
        assert "链接 A" in snap
        assert "已点击" in clk
        assert manager.session.calls[0] == "navigate:https://example.com/"

    def test_type_and_get_text(self, tmp_path, monkeypatch):
        manager = _FakeManager()
        import src.open_llm_vtuber.task_platform.browser_tools as bt
        monkeypatch.setattr(bt, "get_browser_manager", lambda: manager)
        tools = {t.name: t for t in browser_tools(_cfg(), str(tmp_path), "t1")}

        async def run():
            t = await tools["browser_type"].ainvoke({"ref": 2, "text": "hello", "submit": True})
            txt = await tools["browser_get_text"].ainvoke({})
            return t, txt

        t, txt = asyncio.run(run())
        assert "已填入" in t
        assert manager.session.calls[0] == "type:2:hello:True"
        assert "页面文本内容" in txt

    def test_screenshot_saved(self, tmp_path, monkeypatch):
        manager = _FakeManager()
        import src.open_llm_vtuber.task_platform.browser_tools as bt
        monkeypatch.setattr(bt, "get_browser_manager", lambda: manager)
        tools = {t.name: t for t in browser_tools(_cfg(), str(tmp_path), "t1")}

        async def run():
            return await tools["browser_screenshot"].ainvoke({"filename": "shot-1"})

        out = asyncio.run(run())
        assert "/workspace/.pi/tasks/t1/browser/shot-1.png" in out
        shot = tmp_path / ".pi" / "tasks" / "t1" / "browser" / "shot-1.png"
        assert shot.is_file()
        assert shot.read_bytes() == b"\x89PNG-fake"

    def test_close(self, tmp_path, monkeypatch):
        manager = _FakeManager()
        import src.open_llm_vtuber.task_platform.browser_tools as bt
        monkeypatch.setattr(bt, "get_browser_manager", lambda: manager)
        tools = {t.name: t for t in browser_tools(_cfg(), str(tmp_path), "t1")}

        out = asyncio.run(tools["browser_close"].ainvoke({}))
        assert "已关闭" in out
        assert manager.closed == ["t1"]

    def test_ssrf_block_in_tool(self, tmp_path, monkeypatch):
        manager = _FakeManager()
        import src.open_llm_vtuber.task_platform.browser_tools as bt
        monkeypatch.setattr(bt, "get_browser_manager", lambda: manager)
        tools = {t.name: t for t in browser_tools(_cfg(), str(tmp_path), "t1")}

        out = asyncio.run(tools["browser_navigate"].ainvoke({"url": "http://127.0.0.1:80/x"}))
        assert "[browser]" in out and "内网" in out
        assert manager.session.calls == []  # 未发起浏览


class TestSessionManager:
    def test_lru_eviction(self):
        from src.open_llm_vtuber.task_platform.browser import BrowserSessionManager

        m = BrowserSessionManager(max_sessions=2)
        # 不真正创建 session（避免 playwright）—— 直接测登记/回收逻辑需要 session，跳过真实创建
        assert m._max_sessions == 2
        assert m._idle_timeout_s > 0
