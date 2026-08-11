"""`agents/*.md` frontmatter 解析 + 契约校验（plan §8 Phase 6b，参考 dwsy agents/*.md）。

- 契约（内嵌默认，不依赖外部 JSON；与 skills/frontmatter.py 不同 —— skill 用 JSON contract，
  agent 字段集简单固定）：
  - `description`: 必填字符串（委派工具提示用）。
  - `enabled`: 可选 bool，默认 True（False 即从 catalog 隐藏）。
  - `display_name`: 可选字符串（人类可读名）。
  - `tools`: 可选字符串 —— `read`（只读集）/ `all`（全部沙箱工具）/ 逗号分隔工具名。
  - `prompt_mode`: 可选 `replace`（正文=完整 system prompt）/ `append`（主 system prompt + 正文）。
- `parse_agent_md`：返回 (frontmatter dict, body)；缺 frontmatter / YAML 非法 / 契约不符 → None
  （调用方跳过，绝不抛错）。
- 单向依赖：frontmatter.py ← catalog.py；只依赖 yaml / loguru / stdlib。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from loguru import logger

import yaml

#: 允许的 tools 取值（read=只读子集；all=全部；否则逗号分隔工具名）。
TOOLS_READ = "read"
TOOLS_ALL = "all"


def _split_frontmatter(text: str) -> tuple[Optional[str], str]:
    """`---` 包裹的前置块 → (frontmatter 文本, body)；无 frontmatter → (None, text)。"""
    parts = re.split(r"^---\s*$", text, 2, re.MULTILINE)
    if len(parts) < 3 or parts[0].strip():
        return None, text
    _fm, rest = parts[1], parts[2]
    body = rest[1:] if rest.startswith("\n") else rest
    return _fm, body


def _validate(data: dict[str, Any]) -> list[str]:
    """按契约校验 frontmatter，返回错误列表（空 = 合法）。"""
    errors: list[str] = []
    if not isinstance(data.get("description"), str) or not data["description"].strip():
        errors.append("缺必填字段 description")
    for key, allowed in (("enabled", bool), ("display_name", str)):
        if key in data and data[key] is not None and not isinstance(data[key], allowed):
            errors.append(f"{key} 应为 {allowed.__name__}")
    if "tools" in data and not isinstance(data.get("tools"), str):
        errors.append("tools 应为字符串（read/all/逗号分隔工具名）")
    if "prompt_mode" in data and data.get("prompt_mode") not in (None, "replace", "append"):
        errors.append("prompt_mode 应为 replace 或 append")
    return errors


def parse_agent_md(path: Path) -> Optional[tuple[dict[str, Any], str]]:
    """解析单个 `agents/*.md` → (frontmatter dict, body)；非法/契约不符 → None（调用方跳过）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")  # 剥离 BOM
    except OSError as e:
        logger.warning(f"agent: 读取 {path} 失败: {e}")
        return None

    fm_text, body = _split_frontmatter(text)
    if fm_text is None:
        logger.warning(f"agent: {path} 缺 frontmatter，跳过")
        return None
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        logger.warning(f"agent: {path} frontmatter YAML 非法: {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"agent: {path} frontmatter 非映射，跳过")
        return None

    errors = _validate(data)
    if errors:
        logger.warning(f"agent: {path} 契约不符（{', '.join(errors)}），跳过")
        return None

    data.setdefault("enabled", True)
    data.setdefault("tools", TOOLS_READ)
    data.setdefault("prompt_mode", "append")
    return data, body


__all__: list[str] = ["parse_agent_md", "TOOLS_READ", "TOOLS_ALL"]
