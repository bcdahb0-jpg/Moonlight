"""Phase 4：Goal 状态机（plan §5.2 D / §8）。run 外层循环驱动，防死循环。

- `evaluate_goal_completion`：复用主模型（决策 #5：goal_evaluator_model=main）评估
  目标是否达成，输出 JSON `{satisfied, blocker, reason}`；解析失败 fail-soft 视为
  **已完成**（防死循环，且不改变既有 stub 测试的事件序列）。
- `should_continue`：satisfied / blocker≠goal_not_met_yet / 迭代超限 → False。
- `recent_text_signature`：最近 n 条**非空 AI 文本**的 sha256 签名，判"无进展"（同签名
  连续 max_no_progress 轮 → 提示用户澄清并终止，有界）。
- 隐藏 continuation：`CONTINUATION_PROMPT` 以 SystemMessage + `hide_from_ui=True`
  注入（追加进 checkpoint，前端投影忽略，plan §5.5）。
- 单向依赖：goal.py ← task_route.py；仅依赖 langchain_core.messages / loguru / stdlib。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from loguru import logger

#: 目标评估输入上限（对话过长只取最近 N 条，控成本）。
EVAL_MAX_MESSAGES = 20
#: 单条消息截断（LLM 上下文保护）。
_EVAL_MSG_CHAR_LIMIT = 500

#: 隐藏 continuation：agent 续跑提示（SystemMessage，hide_from_ui=True，前端不显示）。
CONTINUATION_PROMPT = (
    "继续执行，直至任务目标达成。若已无法推进，请直接说明当前阻塞点，不要重复之前的内容。"
    "（本消息为系统注入，不显示给用户。）"
)

#: 连续无进展后提示用户澄清（plan §5.2 D：无进展 5 轮后提示）。
CLARIFICATION_MESSAGE = (
    "连续多轮未见实质进展，需要你的澄清：任务目标是否仍然有效？是否需要补充信息或调整目标？"
)


@dataclass
class GoalEval:
    """目标评估结论。satisfied=True 时 blocker 应为空。"""

    satisfied: bool
    blocker: str = ""
    reason: str = ""


#: 评估 prompt（要求只输出一个 JSON 对象）。
_GOAL_EVAL_PROMPT = """\
你是任务验收评估器。请阅读任务目标与已执行的对话，判断目标是否达成。
只输出一个 JSON 对象，不要输出任何其它文字，格式如下：
{{"satisfied": true 或 false, "blocker": "goal_not_met_yet" | "needs_clarification" | "other" | "", "reason": "一句话原因"}}
- satisfied=false 时 blocker 必填；satisfied=true 时 blocker 填空字符串。
- blocker=goal_not_met_yet：还没做完、可继续；needs_clarification：缺信息需用户澄清。

任务目标：{goal}
最近的对话（至多 {max_messages} 条）：
{messages}
"""


def _extract_json(text: str) -> str:
    """容忍模型输出里夹带 ```json 围栏或前后缀文字，提取第一个 {...} 块。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else text


async def evaluate_goal_completion(model: Any, goal: str, messages: list) -> GoalEval:
    """复用主模型评估目标达成情况（plan 决策 #5）。

    失败（模型异常 / 非 JSON）fail-soft 视为已完成——宁可放行也不死循环。
    """
    recent = messages[-EVAL_MAX_MESSAGES:]
    transcript = "\n".join(
        f"{type(m).__name__}: {str(m.content)[:_EVAL_MSG_CHAR_LIMIT]}" for m in recent
    )
    try:
        # format 在 try 内：goal 含 {placeholder} 时抛 KeyError → fail-soft 兜底（review MEDIUM）
        prompt = _GOAL_EVAL_PROMPT.format(
            goal=goal, messages=transcript or "（无对话）", max_messages=EVAL_MAX_MESSAGES
        )
        resp = await model.ainvoke([SystemMessage(content=prompt)])
        text = str(getattr(resp, "content", "") or "").strip()
        data = json.loads(_extract_json(text))
        return GoalEval(
            satisfied=bool(data.get("satisfied", False)),
            blocker=str(data.get("blocker") or ""),
            reason=str(data.get("reason") or ""),
        )
    except Exception as e:  # noqa: BLE001 — fail-soft：评估失败不阻断 run
        logger.warning(f"goal: 目标评估失败，fail-soft 视为已完成（防死循环）: {e}")
        return GoalEval(satisfied=True, blocker="", reason="评估失败，默认已完成")


def should_continue(ev: GoalEval, *, iteration: int, max_iterations: int) -> bool:
    """目标未达成且可推进且未超限 → 续跑；否则停止（防死循环铁律）。"""
    if ev.satisfied:
        return False
    if ev.blocker != "goal_not_met_yet":  # needs_clarification / other / 未知 → 停
        return False
    return iteration < max_iterations


def recent_text_signature(messages: list, n: int = 1) -> str:
    """最近 n 条**非空 AI 文本**的 sha256 签名（判"无进展"；默认只看最新一条）。

    说明：比对整段对话尾部会随消息累积而变化，判不出停滞；只比最新一条 AI 文本
    才能在 agent 反复输出相同结尾时识别"无进展"。
    """
    texts = [str(m.content) for m in messages
             if isinstance(m, AIMessage) and m.content and str(m.content).strip()]
    tail = texts[-n:]
    if not tail:
        return ""
    return hashlib.sha256("␞".join(tail).encode("utf-8")).hexdigest()


__all__: list[str] = [
    "GoalEval",
    "CONTINUATION_PROMPT",
    "CLARIFICATION_MESSAGE",
    "evaluate_goal_completion",
    "should_continue",
    "recent_text_signature",
]
