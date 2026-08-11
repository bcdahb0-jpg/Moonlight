"""SkillCatalog：技能库扫描 + 索引（plan §5.1，G2 两级惰性加载第一级）。

- 扫描 `skills_root/{public,custom}/<name>/SKILL.md`，建 `{name → SkillInfo}` 索引。
  目录名以 `.disabled` 结尾 → 跳过（dwsy 约定，目录改名即禁用，零删除）。
  frontmatter 非法/契约不符（parse_skill_md 返回 None）→ 跳过，其余保留（绝不整库崩）。
- `index_text()`：生成 system prompt 的 `<skill_index>`（`- 名称：描述前 40 字`），
  LLM 只看到名称列表，需要时再 `describe_skill`/`read_skill` 惰性加载。
- `search()`：name+description 关键词匹配（L1 零成本检索；embedding 版 Phase 6）。
- 单向依赖：catalog.py ← frontmatter.py；只依赖 stdlib。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from .frontmatter import parse_skill_md

#: 扫描的分类目录（可扩展：public 内置 / custom 用户自建）。
_SKILL_CATEGORIES = ("public", "custom")
#: 目录名后缀 = 禁用开关（dwsy 约定）。
_DISABLED_SUFFIX = ".disabled"


@dataclass
class SkillInfo:
    """单个技能索引项。body 为 SKILL.md 正文（frontmatter 之后的 Markdown）。"""

    name: str
    description: str
    allowed_tools: list[str] = field(default_factory=list)
    required_secrets: list[str] = field(default_factory=list)
    path: Optional[Path] = None
    body: str = ""


class SkillCatalog:
    """技能索引。每次 scan() 重建；工具/路由共享同一实例。"""

    def __init__(self) -> None:
        self.skills: dict[str, SkillInfo] = {}

    # ------------------------------------------------------------------ //
    # 扫描
    # ------------------------------------------------------------------ //
    @classmethod
    def scan(cls, skills_root: Path) -> "SkillCatalog":
        """扫描技能库根目录，重建索引。skills_root 不存在/为空 → 空索引。"""
        self = cls()
        root = Path(skills_root)
        if not root.is_dir():
            return self
        for category in _SKILL_CATEGORIES:
            cat_dir = root / category
            if not cat_dir.is_dir():
                continue
            for entry in sorted(cat_dir.iterdir()):
                if not entry.is_dir() or entry.name.endswith(_DISABLED_SUFFIX):
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.is_file():
                    continue
                parsed = parse_skill_md(skill_md)
                if parsed is None:
                    continue  # 非法 skill 跳过，其余保留
                fm, body = parsed
                info = SkillInfo(
                    name=str(fm.get("name", entry.name)),
                    description=str(fm.get("description", "")),
                    allowed_tools=[str(t) for t in (fm.get("allowed-tools") or [])],
                    required_secrets=[str(s) for s in (fm.get("required-secrets") or [])],
                    path=skill_md,
                    body=body,
                )
                if info.name in self.skills:
                    logger.warning(
                        f"skill: 重名 {info.name!r}（{self.skills[info.name].path} vs {skill_md}），后者覆盖"
                    )
                self.skills[info.name] = info
        return self

    # ------------------------------------------------------------------ //
    # 查询
    # ------------------------------------------------------------------ //
    def list(self) -> list[SkillInfo]:
        """按名称排序的全部技能。"""
        return sorted(self.skills.values(), key=lambda i: i.name)

    def get(self, name: str) -> Optional[SkillInfo]:
        return self.skills.get(name)

    def names(self) -> list[str]:
        return sorted(self.skills.keys())

    def search(self, query: str) -> list[SkillInfo]:
        """name + description 关键词匹配（大小写不敏感子串，多个词 AND）。"""
        q = query.strip().lower()
        if not q:
            return []
        terms = [t for t in re.split(r"[\s,，]+", q) if t]

        def hit(info: SkillInfo) -> bool:
            hay = (info.name + " " + info.description).lower()
            return all(t in hay for t in terms)

        return [i for i in self.list() if hit(i)]

    def index_text(self) -> str:
        """`<skill_index>`：`- 名称：描述前 40 字`。无技能时给占位提示。"""
        if not self.skills:
            return "（无可用技能）"
        lines = []
        for info in self.list():
            desc = info.description[:40]
            lines.append(f"- {info.name}：{desc}")
        return "\n".join(lines)


__all__: list[str] = ["SkillCatalog", "SkillInfo"]
