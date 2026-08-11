"""Sub-agent 委派系统（plan §8 Phase 6b，参考 dwsy `agents/*.md`）。

- 定义：`agents/*.md` frontmatter 风格（enabled/description/tools/prompt_mode）+ 正文=系统提示。
- 委派：`delegate(agent_name, task)` 工具（agents/tools.py）按名启动一个**临时子 agent**
  （无 checkpointer，ephemeral），用子 agent 的 system prompt + 限定工具集跑完返回结果。
- 内置：`explore`（只读检索）/ `worker`（执行）。
- 单向依赖：agents/* ← graph.py（delegate 工具并入主 agent tools）；不 import models。
"""

from .catalog import AgentCatalog
from .frontmatter import parse_agent_md
from .tools import agent_tools

__all__ = ["AgentCatalog", "agent_tools", "parse_agent_md"]
