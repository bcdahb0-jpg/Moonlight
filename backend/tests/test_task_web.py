"""v3 Phase 2：网页搜索 / 抓取测试（mock httpx，不碰真实网络）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_web.py -q
"""
import asyncio
import types

import pytest

from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig
from src.open_llm_vtuber.task_platform.web import WebClient, web_tools


def _cfg(**overrides):
    base = dict(
        allow_network=True,
        web_search_enabled=True,
        web_search_provider="auto",
        web_search_max_results=5,
        web_fetch_max_bytes=524288,
        web_verify_tls=True,
        tavily_api_key="",
        jina_api_key="",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


class _FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeClient:
    """记录请求的 httpx 假客户端。"""

    def __init__(self, responses=None, default=None):
        self.responses = list(responses or [])
        self.calls = []
        self.default = default

    async def post(self, url, json=None, **kw):
        self.calls.append(("post", url, json))
        if self.responses:
            return self.responses.pop(0)
        return self.default or _FakeResponse()

    async def get(self, url, headers=None, **kw):
        self.calls.append(("get", url, headers))
        if self.responses:
            return self.responses.pop(0)
        return self.default or _FakeResponse()

    async def aclose(self):
        pass


def make_client(responses=None, default=None):
    return _FakeClient(responses, default)


class TestWebSearch:
    def test_tavily_success(self):
        cfg = _cfg(tavily_api_key="tk-1", web_search_provider="auto")
        fake = make_client(responses=[
            _FakeResponse(200, json_data={
                "results": [
                    {"title": "FastAPI Docs", "url": "https://fastapi.tiangolo.com", "content": "Modern framework"},
                ]
            }),
        ])
        wc = WebClient(cfg, client_factory=lambda: fake)

        async def run():
            r = await wc.search("fastapi")
            return r, fake.calls

        r, calls = asyncio.run(run())
        assert "[tavily]" in r
        assert "FastAPI Docs" in r
        assert calls[0][0] == "post"
        assert calls[0][2]["api_key"] == "tk-1"

    def test_tavily_fails_falls_back_ddg(self, monkeypatch):
        cfg = _cfg(tavily_api_key="tk-1", web_search_provider="auto")
        fake = make_client(responses=[_FakeResponse(500, text="boom")])
        wc = WebClient(cfg, client_factory=lambda: fake)

        class _FakeDDGS:
            def __init__(self, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=None):
                return [{"title": "DDG Hit", "href": "https://x.example", "body": "snippet"}]

        import asyncio as _aio

        async def run():
            # 替换 duckduckgo_search 模块（避免真实联网）
            import sys
            mod = types.ModuleType("duckduckgo_search")
            mod.DDGS = _FakeDDGS
            sys.modules["duckduckgo_search"] = mod
            try:
                return await wc.search("fastapi")
            finally:
                sys.modules.pop("duckduckgo_search", None)

        r = asyncio.run(run())
        assert "[ddg]" in r
        assert "DDG Hit" in r

    def test_both_fail_returns_error_text(self):
        cfg = _cfg(tavily_api_key="", web_search_provider="ddg")
        wc = WebClient(cfg, client_factory=lambda: make_client())

        import sys
        mod = types.ModuleType("duckduckgo_search")

        class _RaisingDDGS:
            def __init__(self, timeout=None):
                raise RuntimeError("no network")

        mod.DDGS = _RaisingDDGS
        sys.modules["duckduckgo_search"] = mod
        try:
            r = asyncio.run(wc.search("anything"))
        finally:
            sys.modules.pop("duckduckgo_search", None)
        assert r.startswith("[web] 搜索失败")
        assert "ddg" in r

    def test_provider_ddg_skips_tavily(self):
        cfg = _cfg(web_search_provider="ddg", tavily_api_key="tk-1")
        fake = make_client()  # 若有 tavily 调用会 200 空 → 但不应被调用
        wc = WebClient(cfg, client_factory=lambda: fake)

        import sys
        mod = types.ModuleType("duckduckgo_search")

        class _EmptyDDGS:
            def __init__(self, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=None):
                return []

        mod.DDGS = _EmptyDDGS
        sys.modules["duckduckgo_search"] = mod
        try:
            r = asyncio.run(wc.search("q"))
        finally:
            sys.modules.pop("duckduckgo_search", None)
        assert r == "(无结果)"
        assert fake.calls == []  # tavily 未被调用


class TestWebFetch:
    def test_fetch_plain_text(self):
        cfg = _cfg()
        fake = make_client(default=_FakeResponse(200, text="Hello world body"))
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/a"))
        assert "Hello world body" in r

    def test_fetch_strips_html(self):
        cfg = _cfg()
        fake = make_client(default=_FakeResponse(200, text="<html><body><h1>Title</h1><p>para</p></body></html>"))
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/b"))
        assert "<h1>" not in r
        assert "Title" in r
        assert "para" in r

    def test_fetch_rejects_non_http(self):
        cfg = _cfg()
        wc = WebClient(cfg, client_factory=lambda: make_client())
        r = asyncio.run(wc.fetch("file:///etc/passwd"))
        assert "拒绝" in r
        assert "file" in r

    def test_fetch_http_error(self):
        cfg = _cfg()
        fake = make_client(default=_FakeResponse(404, text="nf"))
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/nf"))
        assert "HTTP 404" in r

    def test_fetch_truncates(self):
        cfg = _cfg(web_fetch_max_bytes=50)
        fake = make_client(default=_FakeResponse(200, text="x" * 1000))
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/big"))
        assert "内容截断" in r
        assert len(r) < 200

    def test_fetch_jina_key_header(self):
        cfg = _cfg(jina_api_key="jk-1")
        fake = make_client(default=_FakeResponse(200, text="ok"))
        wc = WebClient(cfg, client_factory=lambda: fake)
        asyncio.run(wc.fetch("https://example.com/c"))
        assert fake.calls[0][1].startswith("https://r.jina.ai/")
        assert fake.calls[0][2]["Authorization"] == "Bearer jk-1"


class TestFetchDirectFallback:
    """v4 Phase A2：Jina 失败 → HTTP 直抓兜底（带 UA + stdlib 正文提取）。"""

    def test_jina_403_falls_back_to_direct(self):
        """Jina 403（Cloudflare 拦截实测场景）→ 直抓 200 HTML → 提取正文。"""
        cfg = _cfg()
        fake = make_client(responses=[
            _FakeResponse(403, text="Just a moment..."),  # Jina 被拦
            _FakeResponse(200, text="<html><body><script>var x=1;</script><h1>标题</h1><p>正文段落</p></body></html>"),
        ])
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/direct"))
        assert "标题" in r
        assert "正文段落" in r
        assert "var x=1" not in r  # script 被跳过
        # 第二次请求（直抓）带了浏览器 UA
        assert fake.calls[1][1] == "https://example.com/direct"
        assert "Mozilla/5.0" in fake.calls[1][2]["User-Agent"]

    def test_direct_plain_text_passthrough(self):
        """直抓返回非 HTML（JSON/纯文本）→ 原样返回。"""
        cfg = _cfg()
        fake = make_client(responses=[
            _FakeResponse(403, text="blocked"),
            _FakeResponse(200, text='{"name": "moonlight", "version": "1.0"}'),
        ])
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/api"))
        assert '"name": "moonlight"' in r

    def test_both_fail_reports_jina_error(self):
        cfg = _cfg()
        fake = make_client(responses=[
            _FakeResponse(403, text="blocked"),
            _FakeResponse(404, text="nf"),
        ])
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/gone"))
        assert "Jina HTTP 403" in r
        assert "直抓" in r

    def test_direct_empty_html_reported(self):
        cfg = _cfg()
        fake = make_client(responses=[
            _FakeResponse(500, text="err"),
            _FakeResponse(200, text="<html><body></body></html>"),
        ])
        wc = WebClient(cfg, client_factory=lambda: fake)
        r = asyncio.run(wc.fetch("https://example.com/empty"))
        assert "空内容" in r

    def test_verify_tls_config_used(self, monkeypatch):
        """web_verify_tls=false → httpx.AsyncClient(verify=False)。"""
        import httpx as _httpx

        cfg = _cfg(web_verify_tls=False)
        captured: dict = {}
        real_async_client = _httpx.AsyncClient

        def spy(*a, **kw):
            captured.update(kw)
            return real_async_client(*a, **kw)

        monkeypatch.setattr(_httpx, "AsyncClient", spy)
        wc = WebClient(cfg)
        client = asyncio.run(wc._get_client())
        assert captured.get("verify") is False
        asyncio.run(client.aclose())


class TestWebToolsRegistration:
    def test_tools_absent_when_network_disabled(self):
        cfg = _cfg(allow_network=False)
        assert web_tools(cfg) == []

    def test_tools_absent_when_search_disabled(self):
        cfg = _cfg(web_search_enabled=False)
        assert web_tools(cfg) == []

    def test_tools_present_by_default(self):
        cfg = _cfg()
        tools = web_tools(cfg)
        names = {getattr(t, "name", "") for t in tools}
        assert names == {"web_search", "web_fetch"}
