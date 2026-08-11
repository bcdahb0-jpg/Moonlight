"""v3 Phase 7：项目级记忆 DeerMem 测试（Markdown facts + FTS5 + jieba）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_memory.py -q
"""
import json
import os

import pytest

from src.open_llm_vtuber.task_platform.memory.store import (
    MemoryStore,
    get_memory_manager,
    _render_markdown,
    _split_frontmatter,
)


@pytest.fixture()
def ws(tmp_path):
    return tmp_path


@pytest.fixture()
def store(ws):
    return MemoryStore(str(ws))


class TestFactWrite:
    def test_add_writes_markdown_file(self, store, ws):
        fid = store.add(title="测试任务", content="修复了登录 bug", category="task")
        files = list((ws / ".pi" / "memory" / "facts").glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "id:" in text
        assert "修复了登录 bug" in text
        assert fid in text

    def test_list_facts_roundtrip(self, store):
        store.add(title="A", content="内容甲", category="general")
        store.add(title="B", content="内容乙", category="task")
        facts = store.list_facts()
        assert len(facts) == 2
        titles = {f["title"] for f in facts}
        assert titles == {"A", "B"}
        contents = {f["content"] for f in facts}
        assert "内容甲" in contents

    def test_add_returns_unique_ids(self, store):
        a = store.add(title="x", content="1")
        b = store.add(title="y", content="2")
        assert a != b


class TestFtsSearch:
    def test_english_search(self, store):
        store.add(title="fastapi setup", content="installed fastapi and uvicorn", category="task")
        hits = store.search("fastapi")
        assert any("fastapi" in (h["content"] or "").lower() or "fastapi" in h["title"].lower() for h in hits)

    def test_chinese_search_via_jieba(self, store):
        """中文检索：jieba 预切词后 FTS5 可命中。"""
        store.add(title="登录模块", content="修复了用户登录时 token 过期的问题", category="task")
        hits = store.search("登录 token 过期")
        assert len(hits) >= 1, "中文检索应命中"

    def test_no_match_returns_empty(self, store):
        store.add(title="aaa", content="bbb", category="general")
        assert store.search("zzz不存在词") == []

    def test_empty_query(self, store):
        assert store.search("") == []

    def test_workspace_isolated(self, ws):
        """不同 workspace 记忆隔离。"""
        store1 = MemoryStore(str(ws / "ws1"))
        store2 = MemoryStore(str(ws / "ws2"))
        store1.add(title="private", content="ws1 secret", category="task")
        assert store2.search("ws1 secret") == []


class TestInjection:
    def test_get_context_formats(self, store):
        store.add(title="端口约定", content="后端 12393，前端 5173", category="task")
        ctx = store.get_context("端口")
        assert "项目记忆" in ctx
        assert "12393" in ctx

    def test_get_context_truncates(self, store):
        store.add(title="大文件", content="x" * 10000, category="task")
        ctx = store.get_context("大文件", max_tokens=50)  # 50 token * 4 = 200 字符
        assert len(ctx) < 600
        assert "截断" in ctx or len(ctx) < 500

    def test_get_context_empty_when_no_facts(self, store):
        assert store.get_context("anything") == ""


class TestFrontmatter:
    def test_render_and_split_roundtrip(self):
        meta = {"id": "abc", "title": "标题", "confidence": 0.8, "tags": ["a", "b"]}
        md = _render_markdown(meta, "正文内容")
        parsed, body = _split_frontmatter(md)
        assert parsed["id"] == "abc"
        assert parsed["title"] == "标题"
        assert parsed["confidence"] == 0.8
        assert parsed["tags"] == ["a", "b"]
        assert body.strip() == "正文内容"

    def test_split_no_frontmatter(self):
        meta, body = _split_frontmatter("plain text")
        assert meta == {}
        assert body == "plain text"


class TestManager:
    def test_get_memory_manager_caches(self, ws):
        m1 = get_memory_manager(str(ws))
        m2 = get_memory_manager(str(ws))
        assert m1 is m2

    def test_manager_add_search(self, ws):
        mgr = get_memory_manager(str(ws))
        mgr.add(title="deployment", content="uvicorn on 8000", category="task")
        assert len(mgr.search("uvicorn")) >= 1


class TestConfidenceAndDecay:
    def test_higher_confidence_ranks_first(self, store):
        store.add(title="low", content="python version matters", category="general", confidence=0.2)
        store.add(title="high", content="python 3.12 required", category="general", confidence=0.9)
        hits = store.search("python")
        assert hits[0]["title"] == "high", "高 confidence 应排前"
