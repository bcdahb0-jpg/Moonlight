"""任务内核 agent 组装（plan §5.2）：`build_agent` 用 `create_agent` 组装
5 middleware + 沙箱工具 + AsyncSqliteSaver checkpoint。

- system prompt 硬约束（plan §5.3）：文件操作用 ls/glob/grep/read_file/write_file；
  `bash` 仅作无替代命令时的最后手段（避免管道/通配符等 cmd 语义差异）。
- checkpointer：`<workspace>/.pi/tasks/<task_id>/checkpoints.db`（AsyncSqliteSaver，
  异步；同步 SqliteSaver 不支持 ainvoke/astream），续接任务用同一路径重建即恢复上下文。
- 注入 `ToolCallEventMiddleware(bus)`（hooks.py）：工具调用前后发 SSE 事件。
- 单向依赖：graph.py ← task_route.py；import llm_adapter/sandbox/middleware/hooks/
  state/session/conf_bridge（不 import models）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from langchain.agents import create_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from loguru import logger

from . import llm_adapter, mcp_client, middleware
from .acp_client import acp_agent_tools
from .agents.catalog import AgentCatalog
from .agents.tools import agent_tools
from .bash_audit import BashAuditMiddleware
from .browser_tools import browser_tools
from .conf_bridge import TaskPlatformConfig, task_config
from .dangling import DanglingToolCallMiddleware
from .delegation_ledger import DelegationLedgerMiddleware
from .extensions import (
    ExtensionHookMiddleware,
    PluginRegistry,
    apply_before_agent_start,
)
from .hooks import EventBus, ToolCallEventMiddleware
from .memory.store import get_memory_manager
from .output_budget import ToolOutputBudgetMiddleware
from .read_before_write import ReadBeforeWriteMiddleware
from .sandbox import sandbox_tools
from .session import session_dir
from .skills.catalog import SkillCatalog
from .skills.tools import skill_tools
from .state import TaskState
from .token_budget import TokenBudgetMiddleware
from .web import web_tools

#: 任务内核 system prompt（G7：零人设工程提示 + §5.3 工具铁律）。
CORE_SYSTEM_PROMPT = """\
你是任务执行智能体，在受控工作目录 /workspace 内完成用户交办的任务。聚焦任务本身，不套人设，简洁高效。

【工具铁律】
1. 文件操作用 ls/glob/grep/read_file/write_file/str_replace 工具；
2. 修改已有文件：先 read_file 读取当前内容，再用 str_replace 精确替换（不重写整文件）；
   write_file 仅用于新建文件或整文件重写；
3. bash 仅作无替代命令时的最后手段，且避免 shell 语法（管道 |、通配符 *、分号 ; 在 Windows cmd 下语义不同，易出错）；
4. 绝不读写 /workspace 之外的文件；输出路径统一用 /workspace/... 虚拟路径；
5. 需要用户澄清时调用 ask_clarification 工具。用户指令模糊——未指明具体方向/范围/风格/程度（如「改一下」「再优化」「不够」「加点东西」）时，**必须先** ask_clarification 澄清再动手，禁止自行猜测大改方向；只有方向明确、无需确认执行细节时才直接执行；
6. 查资料用 web_search（联网工具仅在 allow_network 开启时可用），再按需 web_fetch 抓正文。

【技能库】（plan §5.1：按需 describe_skill / read_skill 惰性加载）
<skill_index>
__SKILL_INDEX__
</skill_index>

【输出】中文回复；任务完成时给出简短总结。"""


def checkpoint_db_path(workspace: str, task_id: str) -> Path:
    """任务 checkpoint 数据库：<workspace>/.pi/tasks/<task_id>/checkpoints.db"""
    return session_dir(workspace, task_id) / "checkpoints.db"


@asynccontextmanager
async def build_agent(
    workspace: str,
    task_id: str,
    *,
    bus: EventBus | None = None,
    cfg: TaskPlatformConfig | None = None,
    model: Any | None = None,
    extra_tools: tuple = (),
    include_mcp: bool = True,
    mcp_client_factory: Any = None,
    include_agents: bool = True,
    include_acp: bool = True,  # v4 Phase C2：外部 ACP 编码 agent（cfg 无 acp_agents 时自动空）
    goal: str = "",
) -> AsyncIterator[Any]:
    """组装任务内核 agent（create_agent + 5 middleware + 沙箱工具 + MCP + 委派 + AsyncSqliteSaver）。

    用法：
        async with build_agent(ws, tid, bus=bus) as agent:
            await agent.ainvoke({...}, config)

    Args:
        workspace: 任务工作目录（沙箱绑定 + checkpoint 落点）。
        task_id: 任务 id（checkpoint 路径一部分，续接恢复用同一 id）。
        bus: 事件总线（注入 ToolCallEventMiddleware，跑 SSE 时必传）。
        cfg: task_platform 配置（默认 task_config()）。
        model: 覆盖 LLM（测试注入 stub；默认 llm_adapter.build_chat_model(cfg)）。
        extra_tools: 额外工具（Phase 3 skill / 测试专用追加）。
        include_mcp: 是否并入 MCP 工具（plan §5.9；cfg 无 mcp_servers 时自动空）。
        mcp_client_factory: MCP 客户端工厂（测试注入假客户端，绕开真实子进程连接）。
        include_agents: 是否并入 `delegate` 委派工具（plan §8 6b；默认开）。
    """
    cfg = cfg or task_config()
    model = model or llm_adapter.build_chat_model(cfg)
    mcp_tool_list: list = await mcp_client.load_tools(
        cfg, client_factory=mcp_client_factory
    ) if include_mcp else []
    # 同步文件扫描放线程池，避免阻塞事件循环（review MEDIUM：async 热路径）。
    skill_cat, agent_cat, plugin_reg = await asyncio.gather(
        asyncio.to_thread(SkillCatalog.scan, cfg.skills_root_dir),
        asyncio.to_thread(AgentCatalog.load, cfg.agents_root_dir),
        asyncio.to_thread(PluginRegistry.scan_root, cfg.plugins_root_dir),  # plan §8 6c
    )
    sb_tools = list(sandbox_tools(workspace))
    tools = (
        sb_tools
        + skill_tools(skill_cat)        # plan §5.1：describe_skill / read_skill
        + web_tools(cfg)                # v3 Phase 2：web_search / web_fetch（allow_network 门控）
        + (agent_tools(agent_cat, workspace=workspace, model=model,
                       base_prompt=CORE_SYSTEM_PROMPT, sandbox_tool_list=sb_tools,
                       parallel_max=cfg.delegate_parallel_max)  # v4 Phase C1：并行上限
           if include_agents else [])   # plan §8 6b：delegate 委派
        + (acp_agent_tools(cfg, workspace, task_id) if include_acp else [])  # v4 Phase C2
        + browser_tools(cfg, workspace, task_id)  # v4 Phase A3：浏览器自动化（cfg 门控）
        + list(extra_tools)
        + mcp_tool_list
    )
    system_prompt = CORE_SYSTEM_PROMPT.replace("__SKILL_INDEX__", skill_cat.index_text())
    # v3 Phase 7：项目级记忆注入（DeerMem；<memory_context> 块，任务目标作查询）。
    memory_ctx = _memory_context(cfg, workspace, goal)
    if memory_ctx:
        system_prompt += f"\n\n{memory_ctx}\n"
    # plan §8 6c：before_agent_start 钩子（插件可改写 system prompt）
    system_prompt = apply_before_agent_start(
        plugin_reg.api, system_prompt, {"task_id": task_id, "workspace": workspace}
    )

    chain = middleware.build_middleware(model)
    # v3 Phase 6：token 预算前瞻（after_model 累计 token，软警告/硬停）。
    # 插在 Summary 之后（Summary 先压缩再计数，预算语义更准）。
    chain.append(TokenBudgetMiddleware(cfg))
    # v3 Phase 3：dangling tool_call 恢复（wrap_model_call 进模型前 patch 消息历史）。
    # 独立插入：create_agent 的 middleware 列表同时支持 before/after_model 与
    # wrap_model_call 类 hook（DanglingToolCallMiddleware 实现 wrap_model_call）。
    chain.append(DanglingToolCallMiddleware())
    # v4 Phase B2：委派账本注入（wrap_model_call 动态提取历史 delegate 调用渲染账本，
    # 防重复委派；插在 dangling 之后——先修好孤儿消息再提取，账本更准）。
    chain.append(DelegationLedgerMiddleware(max_entries=cfg.delegate_ledger_max_entries))
    if bus is not None:
        chain.insert(0, ToolCallEventMiddleware(bus))  # wrap_tool_call 最外层（先发 tool_call）
    if cfg.read_before_write:
        # v3 Phase 1：写前读版本门（wrap_tool_call 拦截 write_file/str_replace）。
        # 插在事件封装内层：阻断时 error ToolMessage 仍会经事件层标 is_error。
        chain.insert(0, ReadBeforeWriteMiddleware(workspace))
    if cfg.bash_audit:
        # v3 Phase 5：bash 命令审计分级（高危 block / warn 追加警告）。
        chain.insert(0, BashAuditMiddleware())
    if cfg.tool_output_enabled:
        # v4 Phase B1：工具输出预算（>12K 落盘换预览，模型可 read_file 读回）。
        # 与事件层同侧（事件层外层）：tool_result 事件展示的即模型看到的 preview。
        chain.insert(0, ToolOutputBudgetMiddleware(workspace, task_id, cfg))
    if plugin_reg.api.has_event("before_tool_call") or plugin_reg.api.has_event("after_tool_call"):
        # 6c：插件工具钩子放在事件封装外层（before 先于事件发射，block 时不发 tool_call）
        chain.insert(0, ExtensionHookMiddleware(plugin_reg))

    db = checkpoint_db_path(workspace, task_id)
    db.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        agent = create_agent(
            model=model,
            tools=tools,
            state_schema=TaskState,
            middleware=chain,
            system_prompt=system_prompt,
            checkpointer=saver,
        )
        yield agent


def build_model(cfg: TaskPlatformConfig | None = None) -> Any:
    """构建任务主模型（Phase 4：goal 评估复用同一模型，plan 决策 #5）。

    经 graph 暴露给 task_route，维持单向依赖：task_route → graph（不直接 import llm_adapter）。
    """
    cfg = cfg or task_config()
    return llm_adapter.build_chat_model(cfg)


async def available_tools(
    cfg: TaskPlatformConfig,
    *,
    mcp_client_factory: Any = None,
) -> dict[str, Any]:
    """任务 agent 可用工具清单（sandbox/skill/MCP 及状态），供 `GET /api/tasks/tools`。

    - sandbox：白名单工具名。
    - skill：catalog 索引的技能名。
    - mcp：probe_servers 逐服务器状态（connected/error/disabled/misconfigured）。
    单向依赖：task_route.py → graph.available_tools（不直接 import sandbox/mcp_client）。
    """
    Path(cfg.tasks_root_dir).mkdir(parents=True, exist_ok=True)  # Windows realpath 需目录存在
    sandbox_names = [t.name for t in sandbox_tools(str(cfg.tasks_root_dir))]
    catalog = await asyncio.to_thread(SkillCatalog.scan, cfg.skills_root_dir)
    skill_names = [info.name for info in catalog.list()]
    mcp_status = await mcp_client.probe_servers(cfg, client_factory=mcp_client_factory)
    return {"sandbox": sandbox_names, "skill": skill_names, "mcp": mcp_status}


def _memory_context(cfg: TaskPlatformConfig, workspace: str, goal: str) -> str:
    """v3 Phase 7：从项目记忆检索与任务目标相关的 facts，格式化为注入文本。

    - 同步路径（build_agent 组装时）调 FTS 检索（SQLite 读，ms 级，不阻塞）。
    - 检索失败/空 → 返回 ""（fail-soft，不因记忆问题阻断任务）。
    - 记忆落点：<workspace>/.pi/memory/（跟随工作目录，与任务数据同生命周期）。
    """
    try:
        mgr = get_memory_manager(workspace)
        query = goal or workspace  # 无目标时按 workspace 名检索
        return mgr.get_context(query, max_tokens=cfg.memory_max_injection_tokens)
    except Exception as e:  # noqa: BLE001 记忆问题不阻断 build
        logger.warning(f"memory: 记忆注入失败（已跳过）: {e}")
        return ""


def list_skills(cfg: TaskPlatformConfig | None = None) -> list[dict[str, Any]]:
    """`GET /api/skills`：技能索引（名称/描述/允许工具/所需密钥，不含正文）。"""
    cfg = cfg or task_config()
    catalog = SkillCatalog.scan(cfg.skills_root_dir)
    return [
        {
            "name": info.name,
            "description": info.description,
            "allowed_tools": list(info.allowed_tools),
            "required_secrets": list(info.required_secrets),
        }
        for info in catalog.list()
    ]


def list_agents(cfg: TaskPlatformConfig | None = None) -> list[dict[str, Any]]:
    """`GET /api/agents`：sub-agent 目录（名称/描述/display_name/工具集/prompt_mode）。"""
    cfg = cfg or task_config()
    catalog = AgentCatalog.load(cfg.agents_root_dir)
    return [
        {
            "name": info.name,
            "description": info.description,
            "display_name": info.display_name,
            "tools": info.tools,
            "prompt_mode": info.prompt_mode,
        }
        for info in catalog.list()
    ]


def list_plugins(cfg: TaskPlatformConfig | None = None) -> list[dict[str, Any]]:
    """`GET /api/plugins`：已加载插件清单（plan §8 6c）。"""
    cfg = cfg or task_config()
    reg = PluginRegistry.scan_root(cfg.plugins_root_dir)
    return [{"name": name, "enabled": True} for name in reg.loaded]


def skill_detail(name: str, cfg: TaskPlatformConfig | None = None) -> dict[str, Any] | None:
    """`GET /api/skills/{name}`：技能详情（含正文 body）。未找到 → None。"""
    cfg = cfg or task_config()
    catalog = SkillCatalog.scan(cfg.skills_root_dir)
    info = catalog.get(name)
    if info is None:
        return None
    return {
        "name": info.name,
        "description": info.description,
        "allowed_tools": list(info.allowed_tools),
        "required_secrets": list(info.required_secrets),
        "body": info.body,
    }
