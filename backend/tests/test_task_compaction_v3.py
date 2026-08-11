"""v3 Phase 4：compaction 增强测试（token 感知切点 + 结构化摘要 prompt）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_compaction_v3.py -q
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.open_llm_vtuber.task_platform.middleware import (
    SUMMARY_PROMPT_CREATE,
    SUMMARY_PROMPT_UPDATE,
    Summary,
)


def _msg(content: str, cls=HumanMessage, **kw) -> object:
    return cls(content=content, **kw)


class TestTokenAwareCutoff:
    def test_zero_for_empty(self):
        assert Summary.token_aware_cutoff([], 1000) == 0

    def test_cutoff_not_on_tool_message(self):
        """切点永不落在 ToolMessage 上（AI/Tool 对不拆散）。"""
        msgs = [
            _msg("请修改代码", HumanMessage),
            _msg("我来改", AIMessage),
            _msg("ok", ToolMessage, tool_call_id="t1"),
            _msg("改完了", AIMessage),
            _msg("结果", ToolMessage, tool_call_id="t2"),
        ]
        cutoff = Summary.token_aware_cutoff(msgs, keep_recent_tokens=1)
        # 预算极小 → 切点尽量靠前，但必须越过 ToolMessage
        assert 0 <= cutoff < len(msgs)
        if cutoff < len(msgs):
            from langchain_core.messages import ToolMessage as TM

            assert not isinstance(msgs[cutoff], TM)

    def test_large_budget_keeps_most(self):
        msgs = [_msg("m%d" % i, HumanMessage) for i in range(10)]
        # 预算很大 → 全部保留（cutoff=0）
        assert Summary.token_aware_cutoff(msgs, keep_recent_tokens=10**6) == 0

    def test_small_budget_cuts_early(self):
        msgs = [_msg("a" * 400, HumanMessage) for _ in range(10)]
        cutoff = Summary.token_aware_cutoff(msgs, keep_recent_tokens=200, chars_per_token=4)
        # 200 token * 4 字符 = 800 字符预算 → 约切掉前 8 条
        assert 0 < cutoff < 10

    def test_chars_per_token_parameter(self):
        msgs = [_msg("a" * 4000, HumanMessage) for _ in range(3)]
        cutoff_loose = Summary.token_aware_cutoff(msgs, 500, chars_per_token=4)  # 2000 字符
        cutoff_tight = Summary.token_aware_cutoff(msgs, 500, chars_per_token=8)  # 4000 字符
        assert cutoff_loose >= cutoff_tight


class TestStructuredSummaryPrompts:
    def test_create_prompt_has_six_sections(self):
        for sec in ("【目标】", "【约束】", "【进展】", "【关键决策】", "【下一步】", "【关键上下文】"):
            assert sec in SUMMARY_PROMPT_CREATE

    def test_update_prompt_merges_existing(self):
        for sec in ("【目标】", "【约束】", "【进展】", "【关键决策】", "【下一步】", "【关键上下文】"):
            assert sec in SUMMARY_PROMPT_UPDATE
        assert "旧摘要" in SUMMARY_PROMPT_UPDATE

    def test_structured_summary_create(self):
        out = Summary.structured_summary("s", messages="<m>")
        assert "【目标】" in out
        assert "<m>" in out

    def test_structured_summary_update(self):
        out = Summary.structured_summary("s", existing="old", messages="<m>")
        assert "旧摘要" in out
        assert "old" in out
        assert "<m>" in out


class TestSummaryMiddlewareInstantiates:
    def _model(self):
        from types import SimpleNamespace

        return SimpleNamespace(_llm_type="openai-chat")

    def test_builds_with_default_prompt(self):
        mw = Summary(self._model())
        assert mw.summary_prompt == SUMMARY_PROMPT_CREATE
        assert mw.trigger == ("tokens", 8000)
        assert mw.keep == ("messages", 20)

    def test_builds_with_custom_prompt(self):
        mw = Summary(self._model(), summary_prompt="custom {messages}")
        assert "custom" in mw.summary_prompt
