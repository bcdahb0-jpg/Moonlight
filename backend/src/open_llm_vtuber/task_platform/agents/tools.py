"""`delegate` / `delegate_parallel` 委派工具（plan §8 Phase 6b + v4 Phase C1 并行）。

- `delegate(agent_name, task)`：查 catalog → 用子 agent 的 system prompt + 限定工具集，
  在**临时无 checkpoint 图**上跑一遍 `task`，返回最终文本。
- `delegate_parallel(delegations, max_concurrency)`：并发执行多个委派（asyncio.gather +
  Semaphore 限流），返回结构化聚合文本（序号 + agent + 状态 + 结果节选）。
- 子 agent 与主 agent 共享沙箱（同一 workspace），工具集按 frontmatter `tools` 收窄：
  `read`=只读集（ls/glob/grep/read_file）；`all`=全部沙箱工具；逗号分隔=按名过滤。
- `prompt_mode`：`replace` = 正文作为完整 system prompt；`append` = 主 system prompt + 正文。
- 边界：unknown/disabled → 错误文本；递归上限 + 超时兜底；并行单分支失败不拖垮其他分支。
- 子 agent 不再注入 `delegate` 工具 → 不会嵌套委派（单层）。
- 委派结果账本由 DelegationLedgerMiddleware 从消息历史动态提取（v4 Phase B2），
  本模块只负责执行；>12K 的结果由 ToolOutputBudget 自动落盘（v4 Phase B1）。
- 单向依赖：tools.py ← catalog.py / sandbox.py / graph.py（CORE_SYSTEM_PROMPT 由调用方注入）；
  不 import models。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool

from .catalog import AgentCatalog, AgentInfo
from ..delegation_ledger import bound_text
from ..state import TaskState

logger = logging.getLogger(__name__)

#: 子 agent 递归上限（防死循环；主 agent 的 max_iterations=20 不作用于子图）。
DELEGATE_MAX_STEPS = 12
#: 单个子 agent 总超时（秒；与沙箱 bash 超时同量级，防子 agent 卡死阻塞主链路）。
DELEGATE_TIMEOUT_SEC = 180
#: 并行聚合文本里每条结果的节选长度。
_PARALLEL_RESULT_CAP = 800

#: `read` 工具集：只读检索（绝不写盘 / 不执行 bash）。
_READ_ONLY_NAMES = frozenset({"ls", "glob", "grep", "read_file"})


def _resolve_tools(tools_field: str, sandbox_tool_list: list[BaseTool]) -> list[BaseTool]:
    """按 frontmatter `tools` 字段收窄沙箱工具列表。

    - `read` → 只读子集；`all` → 全部沙箱工具（不硬编码名单，新增工具自动包含）；
      逗号分隔名 → 按名过滤（未知名忽略）。
    - 空/异常 → 只读集（安全默认）。
    """
    field = tools_field.strip()
    if field == "all":
        return list(sandbox_tool_list)
    if field == "read":
        return [t for t in sandbox_tool_list if t.name in _READ_ONLY_NAMES]
    names = frozenset(n.strip() for n in field.split(",") if n.strip()) or _READ_ONLY_NAMES
    return [t for t in sandbox_tool_list if t.name in names]


def _build_system_prompt(info: AgentInfo, base_prompt: str) -> str:
    """按 `prompt_mode` 组装子 agent system prompt。

    - replace：正文即完整 prompt（子 agent 独立角色，不继承主 prompt）。
    - append：主 prompt + 正文（继承工具铁律 + 追加子 agent 指引）。
    """
    body = info.body.strip()
    if info.prompt_mode == "replace":
        return body
    return f"{base_prompt}\n\n【子 agent 委派任务指引】\n{body}"


def _last_ai_text(state: dict[str, Any]) -> str:
    """取最终 AI 回复文本；无 AI 消息则回退最后一条消息内容。"""
    for m in reversed(state.get("messages") or []):
        if isinstance(m, AIMessage):
            return str(m.content)
    msgs = state.get("messages") or []
    return str(msgs[-1].content) if msgs else "（子 agent 无输出）"


async def _run_delegate(
    info: AgentInfo,
    task: str,
    *,
    model: Any,
    base_prompt: str,
    sandbox_tool_list: list[BaseTool],
    workspace: str,
    timeout_sec: float = DELEGATE_TIMEOUT_SEC,
) -> tuple[str, str]:
    """执行单个委派：返回 (status, text)。status ∈ done | error | timeout。

    供 `delegate`（单发）与 `delegate_parallel`（并发）复用；超时/异常一律转文本返回，
    永不抛异常（deer-flow 铁律），并行时单分支失败不拖垮其他分支。
    """
    sub_tools = _resolve_tools(info.tools, sandbox_tool_list)
    system_prompt = _build_system_prompt(info, base_prompt)
    sub_agent = create_agent(
        model=model,
        tools=sub_tools,
        state_schema=TaskState,  # 与主 agent 同 schema（workspace 为合法通道）
        middleware=[],
        system_prompt=SystemMessage(content=system_prompt),
        checkpointer=None,  # 临时图，不落 checkpoint
    )
    config = {"recursion_limit": DELEGATE_MAX_STEPS}
    thread = {"configurable": {"thread_id": f"delegate-{info.name}-{uuid.uuid4().hex}"}}
    try:
        state = await asyncio.wait_for(
            sub_agent.ainvoke(
                {"messages": [HumanMessage(content=task)], "workspace": workspace},
                {**config, **thread},
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        return "timeout", f"[delegate] 子 agent {info.name!r} 执行超时（>{timeout_sec}s），已中止"
    except Exception as e:  # noqa: BLE001 工具级兜底：异常吞回文本，不炸主链路
        logger.warning(f"delegate {info.name} failed: {e}")
        return "error", f"[delegate] 子 agent {info.name!r} 执行失败：{e}"
    return "done", _last_ai_text(state)


def agent_tools(
    catalog: AgentCatalog,
    *,
    workspace: str,
    model: Any,
    base_prompt: str,
    sandbox_tool_list: list[BaseTool] | None = None,
    parallel_max: int = 3,
) -> list[BaseTool]:
    """构建 `[delegate, delegate_parallel]` 委派工具（闭包持有 catalog / workspace / model）。

    Args:
        catalog: sub-agent 目录（delegate 查名）。
        workspace: 任务工作目录（子 agent 沙箱绑定）。
        model: 主模型（子 agent 复用同一模型；imodel 支持留待后续）。
        base_prompt: 主 system prompt（prompt_mode=append 时作前缀）。
        sandbox_tool_list: 沙箱工具列表（默认 sandbox_tools(workspace) 重新构建）。
        parallel_max: delegate_parallel 默认并发上限（conf.yaml delegate_parallel_max）。
    """
    if sandbox_tool_list is None:
        from ..sandbox import sandbox_tools

        sandbox_tool_list = sandbox_tools(workspace)

    async def _delegate_once(agent_name: str, task: str) -> str:
        info = catalog.get(agent_name)
        if info is None:
            avail = "，".join(catalog.names()) or "（无）"
            return f"子 agent {agent_name!r} 不存在。可用子 agent：{avail}"
        _status, text = await _run_delegate(
            info, task, model=model, base_prompt=base_prompt,
            sandbox_tool_list=sandbox_tool_list, workspace=workspace,
        )
        return text

    @tool
    async def delegate(agent_name: str, task: str) -> str:
        """委派单个子 agent 执行子任务。

        - agent_name：子 agent 名（可用：{names}）。
        - task：给子 agent 的明确指令（含目标、约束、期望输出）。
        - 返回子 agent 的最终回复文本。适合把"检索/聚焦子任务"拆出去独立执行。
        """
        return await _delegate_once(agent_name, task)

    @tool
    async def delegate_parallel(
        delegations: list[dict[str, str]],
        max_concurrency: int | None = None,
    ) -> str:
        """并发委派多个子 agent 执行独立子任务（互不依赖时用，整体更快）。

        - delegations：[{"agent_name": ..., "task": ...}, ...]，最多 {max} 个；
        - max_concurrency：并发上限（默认 {max}）；
        - 返回按输入顺序的聚合结果：`[1] agent名: 状态/结果节选`。子任务间**不应**
          修改同一文件（并发写会冲突）；适合并行检索/调研/独立模块开发。
        """
        limit = max_concurrency or parallel_max
        if not delegations:
            return "[delegate_parallel] delegations 为空"
        if len(delegations) > 50:
            return f"[delegate_parallel] 一次最多 50 个委派（收到 {len(delegations)}），请分批"
        sem = asyncio.Semaphore(max(1, min(limit, 50)))

        async def _one(idx: int, item: dict[str, Any]) -> str:
            name = str(item.get("agent_name") or "")
            task = str(item.get("task") or "")
            if not name:
                return f"[{idx + 1}] (缺 agent_name) 跳过"
            async with sem:
                info = catalog.get(name)
                if info is None:
                    return f"[{idx + 1}] {name}: 不存在"
                status, text = await _run_delegate(
                    info, task, model=model, base_prompt=base_prompt,
                    sandbox_tool_list=sandbox_tool_list, workspace=workspace,
                )
            brief = bound_text(text, _PARALLEL_RESULT_CAP).replace("\n", " ")
            return f"[{idx + 1}] {name}: [{status}] {brief}"

        results = await asyncio.gather(*(_one(i, d) for i, d in enumerate(delegations)))
        return "\n".join(results)

    return [delegate, delegate_parallel]


__all__: list[str] = ["agent_tools"]
