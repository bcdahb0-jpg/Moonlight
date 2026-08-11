"""Skill 工具：describe_skill / read_skill（plan §5.1，G2 两级惰性加载第二级）。

- `describe_skill(name)`：返回 frontmatter 元数据（描述/允许工具/所需密钥）→ LLM 决定
  是否深入；未知名提示可用列表。
- `read_skill(name)`：返回 SKILL.md 正文指引（LLM 按 describe 命中后加载）；正文超长
  截断防爆（review MEDIUM：ToolMessage 全量进 LLM 上下文）。
- 只读：走 catalog 索引查名，不接触用户输入路径（无路径穿越面）。
- 单向依赖：tools.py ← catalog.py；仅依赖 langchain_core.tools。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from .catalog import SkillCatalog

#: read_skill 正文返回上限（工具指令量级足够；防 SKILL.md 意外超大占满上下文）。
_READ_BODY_LIMIT = 8000


def skill_tools(catalog: SkillCatalog) -> list[BaseTool]:
    """构建 [describe_skill, read_skill] 两个只读工具（闭包持有 catalog）。"""

    @tool
    def describe_skill(name: str) -> str:
        """查看技能元数据（描述、允许工具、所需密钥），按需加载技能库。

        技能名称来自系统提示的「可用技能」列表；本工具不返回完整指引，
        确认适用后再调用 read_skill 获取。
        """
        info = catalog.get(name)
        if info is None:
            avail = "，".join(catalog.names()) or "（无）"
            return f"技能 {name!r} 不存在。可用技能：{avail}"
        allowed = "，".join(info.allowed_tools) if info.allowed_tools else "（不限）"
        secrets = "，".join(info.required_secrets) if info.required_secrets else "（无）"
        return (
            f"技能：{info.name}\n"
            f"描述：{info.description}\n"
            f"允许工具：{allowed}\n"
            f"所需密钥：{secrets}\n"
            f"如需完整指引，调用 read_skill(name='{info.name}')。"
        )

    @tool
    def read_skill(name: str) -> str:
        """读取技能完整指引（SKILL.md 正文），供按步骤执行任务。"""
        info = catalog.get(name)
        if info is None:
            avail = "，".join(catalog.names()) or "（无）"
            return f"技能 {name!r} 不存在。可用技能：{avail}"
        body = info.body or "（技能正文为空）"
        if len(body) > _READ_BODY_LIMIT:
            return body[:_READ_BODY_LIMIT] + f"\n\n（技能正文过长，已截断至 {_READ_BODY_LIMIT} 字。完整内容见 {info.path}）"
        return body

    return [describe_skill, read_skill]


__all__: list[str] = ["skill_tools"]
