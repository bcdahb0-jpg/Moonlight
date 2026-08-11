"""task_platform/skills — Skill 系统（plan §5.1，G2）。

分层（单向依赖）：frontmatter → catalog → tools → graph。
- frontmatter：SKILL.md 解析 + 契约校验（contracts/skill_contract.json）。
- catalog：SkillCatalog 扫描 {public,custom}/ + `.disabled` 跳过 + 索引/search/index_text。
- tools：describe_skill / read_skill 惰性加载工具。

不依赖 models/session/mcpp（单向依赖铁律）。
"""
