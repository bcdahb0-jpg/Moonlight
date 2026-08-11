"""外部 ACP 编码 agent 接入（v4 Phase C2，参考 deer-flow invoke_acp_agent_tool）。

`invoke_acp_agent(agent, prompt)`：按 conf.yaml `task_platform.acp_agents` 配置，
spawn 外部 ACP 兼容 agent 子进程（codex / claude / codex-acp 适配器等），经 ACP 协议
`new_session → prompt` 交互，收集流式文本返回最终回复。

- **独立工作目录**：每个任务在 `<workspace>/.pi/tasks/<task_id>/acp-workspace/`
  下运行，任务间隔离；ACP agent 的产物留在该目录（模型可通过 read_file 读回）。
- **惰性 import**：`agent-client-protocol` 未安装时工具返回提示文本（不炸 run）。
- **权限响应**：`auto_approve_permissions=true` 自动批准（allow_once 优先），否则拒绝
  （由 ACP agent 自身的策略/无权限模式处理）。
- **超时兜底**：单次 prompt 超过 `timeout_seconds` → 终止子进程返回错误文本。
- 工具**永不抛异常**（deer-flow 铁律）：所有失败转 `[acp] ...` 文案。
- >12K 的输出由 ToolOutputBudget 自动落盘（v4 Phase B1）。

单向依赖：acp_client.py ← graph.py；仅 import conf_bridge / session + 惰性 acp。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from langchain_core.tools import BaseTool, tool
from loguru import logger

from .conf_bridge import AcpAgentConfig, TaskPlatformConfig
from .session import session_dir

#: ACP workspace 子目录名（相对 <workspace>/.pi/tasks/<task_id>/）。
_ACP_WORKSPACE_SUBDIR = "acp-workspace"


def _expand_env(raw: dict[str, str]) -> dict[str, str]:
    """环境变量：`$NAME` 值从进程环境展开（deer-flow 同款语法）。"""
    out: dict[str, str] = {}
    for k, v in raw.items():
        if v.startswith("$") and v[1:] in os.environ:
            out[k] = os.environ[v[1:]]
        else:
            out[k] = v
    return out


def _build_permission_response(options: list[Any], *, auto_approve: bool) -> Any:
    """构造 ACP 权限响应：auto_approve → 选 allow_once/allow_always；否则拒绝。"""
    from acp import RequestPermissionResponse
    from acp.schema import AllowedOutcome, DeniedOutcome

    if auto_approve:
        for preferred_kind in ("allow_once", "allow_always"):
            for option in options:
                kind = getattr(option, "kind", None)
                if kind not in (preferred_kind, preferred_kind.replace("_", ""), preferred_kind.replace("_", "-")):
                    continue
                option_id = getattr(option, "option_id", None) or getattr(option, "optionId", None)
                if option_id is None:
                    continue
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", optionId=option_id),
                )
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def _format_error(agent: str, command: str, exc: Exception) -> str:
    """用户可行动的 ACP 调用错误文案（区分 FileNotFoundError）。"""
    if not isinstance(exc, FileNotFoundError):
        return f"[acp] 调用外部 agent {agent!r} 失败：{exc}"
    msg = f"[acp] 调用外部 agent {agent!r} 失败：命令 {command!r} 不在 PATH"
    return f"{msg}。请安装对应 CLI 或修改 conf.yaml `task_platform.acp_agents.{agent}.command`"


class _CollectingClient:
    """最小 ACP 客户端：收集流式文本 + 权限响应策略。"""

    def __init__(self, auto_approve: bool):
        self._chunks: list[str] = []
        self._auto_approve = auto_approve

    @property
    def collected_text(self) -> str:
        return "".join(self._chunks)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        try:
            from acp.schema import TextContentBlock

            if hasattr(update, "content") and isinstance(update.content, TextContentBlock):
                self._chunks.append(update.content.text)
        except Exception:  # noqa: BLE001 忽略解析失败的分片
            pass

    async def request_permission(self, options: list[Any], session_id: str, tool_call: Any, **kwargs: Any) -> Any:
        response = _build_permission_response(options, auto_approve=self._auto_approve)
        outcome = response.outcome.outcome
        if outcome == "selected":
            logger.info(f"acp: 权限自动批准（session {session_id} tool_call {tool_call.tool_call_id}）")
        else:
            logger.warning(
                f"acp: 权限请求被拒（session {session_id}）；"
                f"如需自动批准请设置 auto_approve_permissions: true"
            )
        return response


async def _invoke_acp_agent_once(
    agent_cfg: AcpAgentConfig,
    prompt: str,
    work_dir: str,
) -> str:
    """执行一次 ACP 调用（可被工具包装；所有失败转文本返回）。"""
    try:
        from acp import PROTOCOL_VERSION, Client, text_block, spawn_agent_process
        from acp.schema import ClientCapabilities, Implementation
    except ImportError:
        return (
            "[acp] agent-client-protocol 未安装。"
            "请运行：cd backend && UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv add agent-client-protocol"
        )

    client = _CollectingClient(auto_approve=agent_cfg.auto_approve_permissions)
    args = list(agent_cfg.args)
    env = _expand_env(agent_cfg.env) or None
    cmd = agent_cfg.command
    try:
        async with spawn_agent_process(
            client, cmd, *args, env=env, cwd=work_dir
        ) as (conn, proc):
            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="moonlight-task", title="Moonlight Task Agent", version="1.0.0"),
            )
            session_kwargs: dict[str, Any] = {"cwd": work_dir}
            if agent_cfg.model:
                session_kwargs["model"] = agent_cfg.model
            session = await conn.new_session(**session_kwargs)
            try:
                await asyncio.wait_for(
                    conn.prompt(session_id=session.session_id, prompt=[text_block(prompt)]),
                    timeout=agent_cfg.timeout_seconds,
                )
            except TimeoutError:
                logger.error(f"acp: agent {agent_cfg.name} 超时（>{agent_cfg.timeout_seconds}s），终止子进程")
                return (
                    f"[acp] 外部 agent {agent_cfg.name!r} 超时（>{agent_cfg.timeout_seconds}s），已终止。"
                    f"长任务可在 conf.yaml 调大 acp_agents.{agent_cfg.name}.timeout_seconds"
                )
        result = client.collected_text
        logger.info(f"acp: agent {agent_cfg.name} 返回 {len(result)} 字符")
        return result or f"（外部 agent {agent_cfg.name!r} 无文本输出）"
    except Exception as e:  # noqa: BLE001 工具级兜底
        logger.error(f"acp: agent {agent_cfg.name} 调用失败: {e}")
        return _format_error(agent_cfg.name, cmd, e)


def acp_agent_tools(
    cfg: TaskPlatformConfig,
    workspace: str,
    task_id: str,
) -> list[BaseTool]:
    """构建 `[invoke_acp_agent]` 外部 agent 工具（无 acp_agents 配置时返回空列表）。

    每个任务一个独立 acp-workspace（`<workspace>/.pi/tasks/<task_id>/acp-workspace/`），
    任务间产物互不污染；ACP agent 的产物可用沙箱 read_file 读回（路径在 workspace 内）。
    """
    agents = [a for a in cfg.acp_agents if a.command]
    if not agents:
        return []
    work_dir = str(session_dir(workspace, task_id) / _ACP_WORKSPACE_SUBDIR)
    os.makedirs(work_dir, exist_ok=True)
    by_name = {a.name: a for a in agents}
    agent_lines = "\n".join(f"- {a.name}: command={a.command}" for a in agents)

    @tool
    async def invoke_acp_agent(agent: str, prompt: str) -> str:
        """调用外部 ACP 兼容编码 agent（如 codex / claude）执行独立任务。

        - agent：外部 agent 名（可用：{names}）。
        - prompt：**自包含**的任务描述（目标/约束/期望产物路径）。外部 agent 在
          自己的独立工作目录运行，看不到 /workspace 下的文件，请把所需上下文
          写进 prompt，并让它把产物输出到工作目录（可用 read_file 读回）。
        - 返回外部 agent 的最终回复文本。
        """
        agent_cfg = by_name.get(agent)
        if agent_cfg is None:
            return f"[acp] 未知外部 agent {agent!r}。可用：{', '.join(by_name)}"
        return await _invoke_acp_agent_once(agent_cfg, prompt, work_dir)

    return [invoke_acp_agent]


__all__: list[str] = ["acp_agent_tools"]
