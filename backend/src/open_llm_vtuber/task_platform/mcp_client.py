"""MCP 工具接入（plan §5.9，G8）：MultiServerMCPClient 合并外部 MCP 工具进任务 agent。

- 配置：conf.yaml `task_platform.mcp.servers`（conf_bridge.McpServerConfig），与现有
  `mcp_servers.json` 完全解耦。
- 生命周期：adapter 语义「工具每次调用自行建立会话」→ client 无需常驻，连接只发生在
  get_tools/探测阶段；`load_tools` 是纯 async 函数而非上下文管理器。
- fail-soft：**单服务器**独立 try/except + 连接超时，失败仅记日志、跳过，绝不因 MCP
  失败阻塞后端启动或任务主链路（sandbox+skill 已够跑通演示）。
- 安全：`tool_name_prefix=True` 给工具加 `<server>_` 前缀，避免与 sandbox/skill 工具重名
  互相遮蔽；MCP 工具与 sandbox 工具同过 ToolNode 的 handle_tool_errors 拦截。
- 单向依赖：mcp_client.py ← graph.py；仅 import conf_bridge / langchain_mcp_adapters /
  langchain_core（不 import models / session）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger

from .conf_bridge import McpServerConfig, TaskPlatformConfig

#: 单服务器连接超时（stdio 子进程握手 / 远程握手），超时视为失败 fail-soft。
_CONNECT_TIMEOUT_SEC = 20


def _exception_text(exc: BaseException, *, max_length: int = 1000) -> str:
    """展开 ExceptionGroup，保留真正导致 MCP 探测失败的底层原因。"""
    if isinstance(exc, BaseExceptionGroup):
        parts = [_exception_text(child, max_length=max_length) for child in exc.exceptions]
        text = "; ".join(part for part in parts if part)
    else:
        detail = str(exc).strip()
        text = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return " ".join(text.split())[:max_length]


def server_connection(s: McpServerConfig) -> dict[str, Any]:
    """McpServerConfig → adapter Connection 字典；缺关键字段/不支持的 transport 返回空。"""
    if not s.enabled:
        return {}
    if s.transport == "stdio":
        if not s.command:
            logger.warning(f"mcp: server {s.name} stdio 缺 command，跳过")
            return {}
        return {"transport": "stdio", "command": s.command, "args": list(s.args)}
    if s.transport in ("http", "streamable_http", "streamable-http", "sse", "websocket"):
        if not s.url:
            logger.warning(f"mcp: server {s.name} {s.transport} 缺 url，跳过")
            return {}
        conn: dict[str, Any] = {"transport": s.transport, "url": s.url}
        if s.headers:
            conn["headers"] = dict(s.headers)
        return conn
    logger.warning(f"mcp: server {s.name} transport={s.transport!r} 不支持，跳过")
    return {}


def _client_for(cfg: TaskPlatformConfig) -> MultiServerMCPClient:
    """按配置构造 adapter 客户端（工具名加 `<server>_` 前缀防冲突）。"""
    connections: dict[str, dict[str, Any]] = {}
    for s in cfg.mcp_servers:
        conn = server_connection(s)
        if conn:
            connections[s.name] = conn
    return MultiServerMCPClient(connections, tool_name_prefix=True)


async def _load_server_tools(
    client: Any,
    name: str,
    timeout: float,
) -> list[BaseTool]:
    """单服务器工具加载，独立 try/except + 超时（per-server fail-soft）。"""
    try:
        return await asyncio.wait_for(client.get_tools(server_name=name), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"mcp: server {name} 连接超时（>{timeout:.0f}s，fail-soft）")
        return []
    except Exception as e:  # noqa: BLE001 —— fail-soft 铁律：绝不因 MCP 阻塞主链路
        logger.warning(f"mcp: server {name} 加载工具失败（fail-soft）: {e}")
        return []


async def load_tools(
    cfg: TaskPlatformConfig,
    *,
    client_factory: Callable[[TaskPlatformConfig], Any] | None = None,
    timeout_sec: float = _CONNECT_TIMEOUT_SEC,
) -> list[BaseTool]:
    """加载并合并全部启用 MCP 服务器的工具。单服务器失败跳过；无配置则返回空。

    Args:
        cfg: task_platform 配置（mcp_servers 来自 conf.yaml）。
        client_factory: 客户端工厂（测试注入假客户端；默认真实 MultiServerMCPClient）。
        timeout_sec: 单服务器连接超时。
    """
    servers = [s for s in cfg.mcp_servers if s.enabled]
    if not servers:
        return []
    client = (client_factory or _client_for)(cfg)
    merged: list[BaseTool] = []
    for s in servers:
        merged.extend(await _load_server_tools(client, s.name, timeout_sec))
    return merged


async def probe_servers(
    cfg: TaskPlatformConfig,
    *,
    client_factory: Callable[[TaskPlatformConfig], Any] | None = None,
    timeout_sec: float = _CONNECT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """MCP 服务器可用性状态（供 `GET /api/tasks/tools`）。单服务器失败 fail-soft。

    每条：{name, transport, enabled, status, tool_count?, tools?, error?}
    status ∈ connected | error | disabled | misconfigured。
    """
    out: list[dict[str, Any]] = []
    servers = [s for s in cfg.mcp_servers]
    if not servers:
        return out
    try:
        client = (client_factory or _client_for)(cfg)
    except Exception as e:  # noqa: BLE001 —— fail-soft：构造失败逐服务器记为 error
        logger.warning(f"mcp: 客户端构造失败（fail-soft）: {e}")
        for s in servers:
            entry: dict[str, Any] = {
                "name": s.name,
                "transport": s.transport,
                "enabled": s.enabled,
            }
            if not s.enabled:
                entry["status"] = "disabled"
            elif not server_connection(s):
                entry["status"] = "misconfigured"
            else:
                entry["status"] = "error"
                entry["error"] = _exception_text(e)
            out.append(entry)
        return out
    for s in servers:
        entry: dict[str, Any] = {
            "name": s.name,
            "transport": s.transport,
            "enabled": s.enabled,
        }
        if not s.enabled:
            entry["status"] = "disabled"
            out.append(entry)
            continue
        if not server_connection(s):
            entry["status"] = "misconfigured"
            out.append(entry)
            continue
        try:
            tools = await asyncio.wait_for(
                client.get_tools(server_name=s.name), timeout=timeout_sec
            )
            entry["status"] = "connected"
            entry["tool_count"] = len(tools)
            entry["tools"] = [t.name for t in tools]
        except asyncio.TimeoutError:
            entry["status"] = "error"
            entry["error"] = f"timeout after {timeout_sec:.0f}s"
        except Exception as e:  # noqa: BLE001
            entry["status"] = "error"
            entry["error"] = _exception_text(e)
        out.append(entry)
    return out


__all__: list[str] = [
    "load_tools",
    "probe_servers",
    "server_connection",
    "McpServerConfig",
]
