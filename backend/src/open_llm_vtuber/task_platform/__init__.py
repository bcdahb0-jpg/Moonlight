"""任务制智能体平台（task_platform）。

参考 deer-flow 2.0 / pi-agent / dwsy-agent 实现思路构筑（非合并），
详见 docs/agent-harness-platform-plan.md（v2.5）。

模块严格单向依赖：
- models.py <- 全部（实体/事件类型）
- llm_adapter.py <- graph/session
- sandbox.py <- graph/middleware（工具执行唯一副作用出口）
- mcp_client.py <- graph（只做工具合并；失败 fail-soft）
- task_route.py -> 只调 session/graph/models，不碰 conversations/memory/mcp
"""

from . import models  # noqa: F401
