"""Sub-agent 目录（plan §8 Phase 6b）：扫描 `agents/*.md`，仅保留 `enabled: true`。

- 布局：根目录下扁平 `*.md`（dwsy `agents/*.md` 同款）；`*.disabled` 后缀文件跳过
  （dwsy extensions 思想：后缀即开关，plan §8 6c 同）。
- `enabled: false` frontmatter → 不列入（保留定义文件，开关在 frontmatter）。
- 非法定义（缺 frontmatter / 契约不符）→ 跳过并告警，绝不抛错（fail-skip）。
- 单向依赖：catalog.py ← tools.py / graph.py；只依赖 frontmatter + loguru + stdlib。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

from loguru import logger

from .frontmatter import parse_agent_md


@dataclasses.dataclass(frozen=True)
class AgentInfo:
    """一个 sub-agent 定义（frontmatter + 正文路径）。"""

    name: str  # 文件名去 .md（如 explore）
    description: str
    body: str
    enabled: bool = True
    display_name: Optional[str] = None
    tools: str = "read"
    prompt_mode: str = "append"
    path: str = ""


class AgentCatalog:
    """`agents/*.md` 扫描目录。`scan` 幂等，支持 hot-reload（每次调用重扫）。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._index: dict[str, AgentInfo] = {}

    def scan(self) -> "AgentCatalog":
        """扫描 root 下 `*.md`（跳过 `*.disabled`），重建 name→info 索引。"""
        self._index = {}
        if not self.root.is_dir():
            return self
        for p in sorted(self.root.glob("*.md")):
            if p.name.endswith(".disabled.md"):
                continue
            parsed = parse_agent_md(p)
            if parsed is None:
                continue
            data, body = parsed
            name = p.stem
            if not data.get("enabled", True):
                logger.info(f"agent: {name} 被 frontmatter 禁用（enabled: false），不列入")
                continue
            self._index[name] = AgentInfo(
                name=name,
                description=str(data.get("description", "")),
                body=body,
                enabled=True,
                display_name=data.get("display_name"),
                tools=str(data.get("tools", "read")),
                prompt_mode=str(data.get("prompt_mode", "append")),
                path=str(p),
            )
        return self

    def get(self, name: str) -> Optional[AgentInfo]:
        return self._index.get(name)

    def names(self) -> list[str]:
        return sorted(self._index)

    def list(self) -> list[AgentInfo]:
        return [self._index[n] for n in self.names()]

    @staticmethod
    def load(root: str | Path) -> "AgentCatalog":
        """便捷：scan 一次返回 catalog。"""
        return AgentCatalog(root).scan()


__all__: list[str] = ["AgentCatalog", "AgentInfo"]
