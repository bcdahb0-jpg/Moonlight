"""Phase 4：Goal 状态机测试（TDD，plan §5.2 D / §8 Phase 4 验收）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_goal.py -q --basetemp=./.pytest-tmp

覆盖（plan §8 Phase 4 验收）：
- evaluate_goal_completion：复用主模型评估，解析 JSON{satisfied,blocker,reason}；
  解析失败 fail-soft 视为已完成（防死循环）；容忍 ```json 围栏。
- should_continue：satisfied / blocker≠goal_not_met_yet / 超限 → False，否则 True。
- recent_text_signature：最近 AI 文本 sha256 签名（最新一条，判停滞用）。
- run 集成：目标达成自动 run_end(completed)；未达成续跑（隐藏 continuation）；
  无进展 N 轮后提示澄清；空目标单轮完成；无死循环。
"""
import asyncio

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import models, task_route
from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig
from src.open_llm_vtuber.task_platform.goal import (
    GoalEval,
    evaluate_goal_completion,
    recent_text_signature,
    should_continue,
)


# --------------------------------------------------------------------------- #
# 脚手架
# --------------------------------------------------------------------------- #
class _StubModel(BaseChatModel, BaseModel):
    responses: list[BaseMessage] = Field(default_factory=list)
    i: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        n = self.i
        self.i += 1
        msg = self.responses[n % len(self.responses)] if self.responses else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _stub_model(*responses: BaseMessage) -> _StubModel:
    return _StubModel(responses=list(responses))


class _FakeEvaluator:
    """goal_evaluator 替身：按队列返回 GoalEval；单个结果则恒返回。记录调用次数。"""

    def __init__(self, *results: GoalEval):
        self._results = list(results) or [GoalEval(satisfied=True)]
        self.calls = 0

    async def __call__(self, model, goal, messages):
        self.calls += 1
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离元数据库 + 已建任务 + 临时 workspace；用后清理编排注册表。"""
    import src.open_llm_vtuber.task_platform.conf_bridge as cb

    db = tmp_path / "meta.db"
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(cb, "_cached", TaskPlatformConfig(db_path=str(db)))
    models.init_db()
    task = models.create_task(title="t", workspace=str(ws), goal="g")
    yield {"db": db, "ws": ws, "task_id": task.id, "cfg": cb.task_config()}

    for ctx in list(task_route._runs.values()):
        if ctx.run_task is not None and not ctx.run_task.done():
            ctx.run_task.cancel()
    task_route._runs.clear()
    task_route._buses.clear()


def _event_types(tid: str) -> list[str]:
    return [e.event_type for e in models.events_after(tid, 0)]


def _assistant_messages(tid: str) -> list[str]:
    return [e.payload["content"] for e in models.events_after(tid, 0)
            if e.event_type == "message" and e.payload.get("role") == "assistant"]


# --------------------------------------------------------------------------- #
# 1. evaluate_goal_completion
# --------------------------------------------------------------------------- #
class TestEvaluateGoalCompletion:
    def test_satisfied(self):
        model = _stub_model(AIMessage(content='{"satisfied": true, "blocker": "", "reason": "搞定"}'))
        ev = asyncio.run(evaluate_goal_completion(model, "目标", [AIMessage(content="做完了")]))
        assert ev.satisfied is True

    def test_not_satisfied_blocker(self):
        model = _stub_model(AIMessage(content='{"satisfied": false, "blocker": "goal_not_met_yet", "reason": "还没做"}'))
        ev = asyncio.run(evaluate_goal_completion(model, "目标", [AIMessage(content="还没做完")]))
        assert ev.satisfied is False
        assert ev.blocker == "goal_not_met_yet"
        assert "还没做" in ev.reason

    def test_json_fenced_in_code_block(self):
        model = _stub_model(
            AIMessage(content='```json\n{"satisfied": false, "blocker": "needs_clarification", "reason": "缺信息"}\n```')
        )
        ev = asyncio.run(evaluate_goal_completion(model, "目标", []))
        assert ev.satisfied is False
        assert ev.blocker == "needs_clarification"

    def test_parse_failure_fail_soft_satisfied(self):
        """非 JSON 输出 → 视为已完成（fail-soft 防死循环）。"""
        model = _stub_model(AIMessage(content="我还需要继续"))
        ev = asyncio.run(evaluate_goal_completion(model, "目标", [AIMessage(content="x")]))
        assert ev.satisfied is True

    def test_model_error_fail_soft_satisfied(self):
        class _Boom(BaseChatModel, BaseModel):
            @property
            def _llm_type(self) -> str:
                return "boom"

            def _generate(self, messages, **kwargs):
                raise RuntimeError("model down")

        ev = asyncio.run(evaluate_goal_completion(_Boom(), "目标", []))
        assert ev.satisfied is True  # 评估失败不阻断 run

    def test_goal_with_format_placeholder_fail_soft(self):
        """review MEDIUM：goal 含 {placeholder} 时 .format 抛 KeyError → fail-soft 视为已完成。"""
        model = _stub_model(AIMessage(content="ok"))
        ev = asyncio.run(evaluate_goal_completion(model, "重构 {filename}.py", [AIMessage(content="x")]))
        assert ev.satisfied is True


# --------------------------------------------------------------------------- #
# 2. should_continue
# --------------------------------------------------------------------------- #
class TestShouldContinue:
    def test_satisfied_stops(self):
        assert should_continue(GoalEval(satisfied=True), iteration=1, max_iterations=5) is False

    def test_goal_not_met_yet_continues(self):
        ev = GoalEval(satisfied=False, blocker="goal_not_met_yet")
        assert should_continue(ev, iteration=1, max_iterations=5) is True

    def test_needs_clarification_stops(self):
        ev = GoalEval(satisfied=False, blocker="needs_clarification")
        assert should_continue(ev, iteration=1, max_iterations=5) is False

    def test_iteration_limit_stops(self):
        ev = GoalEval(satisfied=False, blocker="goal_not_met_yet")
        assert should_continue(ev, iteration=5, max_iterations=5) is False


# --------------------------------------------------------------------------- #
# 3. recent_text_signature
# --------------------------------------------------------------------------- #
class TestRecentTextSignature:
    def test_latest_ai_text_signature(self):
        msgs = [AIMessage(content="第一步"), AIMessage(content="第二步"), AIMessage(content="第二步")]
        assert recent_text_signature(msgs) == recent_text_signature([AIMessage(content="第二步")])
        assert recent_text_signature(msgs) != recent_text_signature([AIMessage(content="第三步")])

    def test_empty_returns_empty(self):
        assert recent_text_signature([]) == ""
        assert recent_text_signature([AIMessage(content="")]) == ""


# --------------------------------------------------------------------------- #
# 4. run 集成：目标状态机
# --------------------------------------------------------------------------- #
class TestRunGoalMachine:
    def test_satisfied_finishes_single_iteration(self, env):
        tid = env["task_id"]
        evaluator = _FakeEvaluator(GoalEval(satisfied=True))

        async def scenario():
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "做任务",
                model=_stub_model(AIMessage(content="完成")), goal_evaluator=evaluator,
            )
            await ctx.run_task
            assert models.get_run(ctx.run.id).status == "completed"
            assert _event_types(tid) == ["run_start", "message", "message", "run_end"]
            assert evaluator.calls == 1, "目标达成 → 只评估一次"

        asyncio.run(scenario())

    def test_not_satisfied_then_satisfied_continues(self, env):
        tid = env["task_id"]
        evaluator = _FakeEvaluator(
            GoalEval(satisfied=False, blocker="goal_not_met_yet", reason="未完成"),
            GoalEval(satisfied=True),
        )

        async def scenario():
            ctx = task_route.start_run(
                models.get_task(tid), env["cfg"], "做任务",
                model=_stub_model(AIMessage(content="完成")), goal_evaluator=evaluator,
            )
            await ctx.run_task
            assert models.get_run(ctx.run.id).status == "completed"
            types = _event_types(tid)
            assert types[0] == "run_start" and types[-1] == "run_end"
            # 两轮各发一条 assistant 消息（隐藏 continuation 不发事件）
            assert _assistant_messages(tid) == ["完成", "完成"]
            assert evaluator.calls == 2, "未达成 → 续跑一次再评估"

        asyncio.run(scenario())

    def test_no_progress_triggers_clarification_and_terminates(self, env, tmp_path, monkeypatch):
        """连续无进展（同一 AI 文本）→ N 轮后提示澄清并结束（有界，无死循环）。"""
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        cfg = TaskPlatformConfig(db_path=str(tmp_path / "meta.db"), max_no_progress=3)
        monkeypatch.setattr(cb, "_cached", cfg)
        models.init_db()
        task = models.create_task(title="t", workspace=str(env["ws"]), goal="g")
        evaluator = _FakeEvaluator(GoalEval(satisfied=False, blocker="goal_not_met_yet"))

        async def scenario():
            ctx = task_route.start_run(
                task, cfg, "做任务",
                model=_stub_model(AIMessage(content="卡住")), goal_evaluator=evaluator,
            )
            await ctx.run_task
            assert models.get_run(ctx.run.id).status == "completed"
            msgs = _assistant_messages(task.id)
            # 3 轮 "卡住" + 1 条澄清
            assert len([m for m in msgs if m == "卡住"]) == 3, msgs
            assert any("澄清" in m for m in msgs), f"应有澄清提示：{msgs}"
            assert evaluator.calls == 3, "达到无进展上限即停止评估，不无限循环"
            # review LOW：发 clarify_requested 事件，前端可区分「需澄清」与「正常完成」
            types = [e.event_type for e in models.events_after(task.id, 0)]
            assert "clarify_requested" in types, f"应发 clarify_requested 事件：{types}"

        asyncio.run(scenario())

    def test_empty_goal_single_iteration_no_eval(self, env):
        """空目标 → 单轮完成，不做目标评估。"""
        cfg = env["cfg"]
        models.init_db()
        task = models.create_task(title="t", workspace=str(env["ws"]), goal="")
        evaluator = _FakeEvaluator(GoalEval(satisfied=False, blocker="goal_not_met_yet"))

        async def scenario():
            ctx = task_route.start_run(
                task, cfg, "直接干",
                model=_stub_model(AIMessage(content="搞定")), goal_evaluator=evaluator,
            )
            await ctx.run_task
            assert models.get_run(ctx.run.id).status == "completed"
            assert _event_types(task.id) == ["run_start", "message", "message", "run_end"]
            assert evaluator.calls == 0, "无目标 → 不评估"

        asyncio.run(scenario())
