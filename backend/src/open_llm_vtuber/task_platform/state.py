"""任务内核状态 schema（plan §5.2，deer-flow ThreadState 精简版）。

单向依赖：state.py 只依赖 langchain/langgraph，不依赖 task_platform 其他模块。
"""

from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict

from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import AnyMessage
from langgraph.channels.ephemeral_value import EphemeralValue
from langgraph.graph.message import add_messages

#: after_model / before_model 钩子可用跳转目标（langchain 1.3 AgentMiddleware 契约）
JumpTo = str  # "tools" | "model" | "end"


class TaskState(TypedDict, total=False):
    """任务内核状态。

    - `messages`：LangGraph 消息列表（add_messages reducer，跨节点累积）。
    - `goal`：任务目标（新建任务时传入，Goal 状态机消费，Phase 4）。
    - `workspace`：任务工作目录绝对路径（沙箱绑定）。
    - `skill_context`：当前技能上下文（describe_skill 结果，Phase 3）。
    - `summary`：运行摘要（Summary middleware / Phase 6 压缩）。
    - `jump_to`：中间件钩子条件跳转目标（EphemeralValue，跨步不持久化）。
    """

    messages: Annotated[list[AnyMessage], add_messages]
    goal: str
    workspace: str
    skill_context: str
    summary: str
    jump_to: NotRequired[Annotated[JumpTo | None, EphemeralValue, PrivateStateAttr]]
