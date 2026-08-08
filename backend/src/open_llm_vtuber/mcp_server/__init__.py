"""Moonlight MCP 服务端。

让外部 Agent（Claude、OpenClaw 等）通过 Model Context Protocol 反向控制桌宠：
说话、触发情绪/表情、查询状态、截屏。

- ``build_server`` 构建 FastMCP 实例（4 个工具）。
- ``start_mcp_server`` / ``stop_mcp_server`` 用后台 uvicorn 线程拉起/停止
  Streamable HTTP 传输（127.0.0.1:12394，Bearer token 鉴权）。
"""

from .launcher import start_mcp_server, stop_mcp_server
from .server import build_server

__all__ = ["build_server", "start_mcp_server", "stop_mcp_server"]
