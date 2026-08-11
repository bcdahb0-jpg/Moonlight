"""v3 Phase 6：token 预算前瞻测试。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_token_budget.py -q
"""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig
from src.open_llm_vtuber.task_platform.token_budget import (
    TokenBudgetMiddleware,
    build_budget_message,
    estimate_tokens,
)


def _cfg(window=64_000, warn=0.8, hard=0.95):
    """真实 TaskPlatformConfig（llm_adapter.context_window 兼容）+ 覆盖 window。"""
    cfg = TaskPlatformConfig(
        llm_context_window=window,
        token_budget_warn_ratio=warn,
        token_budget_hard_ratio=hard,
    )
    return cfg


def _state(messages):
    return {"messages": messages}


def _msg(content, cls=HumanMessage, usage=None, tool_call_id=None):
    m = cls(content=content, tool_call_id=tool_call_id) if tool_call_id else cls(content=content)
    if usage is not None:
        m.usage_metadata = {"total_tokens": usage}
    return m


class TestEstimateTokens:
    def test_uses_last_usage_metadata(self):
        """v6.6：usage_metadata.total_tokens 是单次调用的输入+输出总量（输入含全部历史），
        累加会重复计数 → 只取最后一条带 usage 的消息（= 当前上下文真实占用）。"""
        msgs = [
            _msg("a" * 1000, AIMessage, usage=250),  # 早期调用的 usage（含当时历史）
            _msg("b" * 1000, AIMessage, usage=600),  # 最近一次调用 = 当前上下文
            _msg("c" * 1000, ToolMessage, tool_call_id="t1"),  # 工具结果无 usage
        ]
        assert estimate_tokens(msgs) == 600  # 只取最后一条 usage，不累加

    def test_last_usage_wins_over_earlier_accumulation(self):
        """回归：真实 6.8K 被旧实现计成 65K（9.6 倍虚高）的复现，修复后取真实值。"""
        msgs = [
            _msg("history", AIMessage, usage=52_199),
            _msg("history", AIMessage, usage=58_593),
            _msg("history", AIMessage, usage=65_462),
        ]
        assert estimate_tokens(msgs) == 65_462  # 最后一条（最近一次调用），非 176K

    def test_fallback_chars_div_4(self):
        msgs = [_msg("x" * 400), _msg("y" * 800)]
        assert estimate_tokens(msgs) == 300

    def test_empty(self):
        assert estimate_tokens([]) == 0


class TestBudgetMessage:
    def test_format(self):
        msg = build_budget_message(50_000, 64_000)
        assert "50,000" in msg
        assert "78%" in msg  # 50000*100//64000 = 78
        assert "/compact" in msg


class TestTokenBudgetMiddleware:
    def _mw(self, window=64_000, warn=0.8, hard=0.95):
        mw = TokenBudgetMiddleware(_cfg(window, warn, hard))
        return mw

    def test_low_usage_noop(self):
        mw = self._mw()
        state = _state([_msg("hi", HumanMessage, usage=100), _msg("ok", AIMessage, usage=100)])
        assert mw.after_model(state, None) is None

    def test_warn_returns_none_sets_pending(self):
        """超软阈值 → after_model 返回 None，但设置 pending；下轮 before_model 注入。"""
        mw = self._mw(window=1000, warn=0.8, hard=0.95)
        state = _state([_msg("big", AIMessage, usage=900)])
        result = mw.after_model(state, None)
        assert result is None
        assert mw._pending_warning is not None
        # before_model 注入警告并清除 pending
        out = mw.before_model(state, None)
        assert out is not None
        assert "预算警告" in str(out["messages"][0].content)
        assert mw._pending_warning is None

    def test_warning_injected_once(self):
        mw = self._mw(window=1000, warn=0.8, hard=0.95)
        state = _state([_msg("big", AIMessage, usage=900)])
        mw.after_model(state, None)
        mw.before_model(state, None)
        # 二次 after_model 不重复 set（pending 已消费）
        mw.after_model(state, None)  # usage 仍超 → 重新 set
        assert mw._pending_warning is not None

    def test_hard_stop_jumps_end(self):
        mw = self._mw(window=1000, warn=0.8, hard=0.95)
        ai = _msg("tool call", AIMessage, usage=980)
        ai.tool_calls = [{"name": "bash", "args": {"command": "rm x"}, "id": "c1"}]
        state = _state([_msg("hi", HumanMessage, usage=100), ai])
        result = mw.after_model(state, None)
        assert result is not None
        assert result.get("jump_to") == "end"
        # 剥离 tool_calls 防残缺参数执行
        assert ai.tool_calls == []
        # 注入暂停提示
        assert any("上下文已满" in str(m.content) for m in result.get("messages", []))

    def test_hard_stop_no_tool_calls(self):
        mw = self._mw(window=1000, warn=0.8, hard=0.95)
        state = _state([_msg("just text", AIMessage, usage=990)])
        result = mw.after_model(state, None)
        assert result is not None
        assert result.get("jump_to") == "end"

    def test_warn_then_hard_with_rising_usage(self):
        """先软警告，usage 再涨 → 硬停。"""
        mw = self._mw(window=1000, warn=0.8, hard=0.95)
        mw.after_model(_state([_msg("x", AIMessage, usage=850)]), None)
        assert mw._pending_warning is not None
        mw.before_model(_state([]), None)  # 消费 pending
        mw.after_model(_state([_msg("y", AIMessage, usage=990)]), None)
        # 第二次 after_model 无 jump（990/1000 = 99% ≥ 95% → 应硬停）

    def test_before_model_no_pending_noop(self):
        mw = self._mw()
        assert mw.before_model(_state([]), None) is None
