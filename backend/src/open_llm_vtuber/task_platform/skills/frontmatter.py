"""SKILL.md frontmatter 解析 + 契约校验（plan §5.1，G2）。

- 契约：`backend/contracts/skill_contract.json`（deer-flow 同款）。字段白名单：
  `name`/`description` 必填字符串；`allowed-tools`/`required-secrets` 可选字符串数组。
  契约文件缺失/损坏时回退内置默认（不阻塞启动）。
- `parse_skill_md`：读取 SKILL.md，返回 (frontmatter dict, body)；frontmatter 缺失、
  YAML 非法、契约不符 → 返回 None（调用方跳过该 skill，绝不抛错）。
- 单向依赖：frontmatter.py ← catalog.py；只依赖 yaml / loguru / stdlib。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from loguru import logger

import yaml

# backend/（skills/frontmatter.py → parents[4]）
_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_CONTRACT_PATH = _BACKEND_ROOT / "contracts" / "skill_contract.json"

#: 契约文件缺失/损坏时的内置回退（与 contracts/skill_contract.json 保持一致）。
_DEFAULT_CONTRACT: dict[str, Any] = {
    "version": 1,
    "fields": {
        "name": {"required": True, "type": "string"},
        "description": {"required": True, "type": "string", "max_chars": 120},
        "allowed-tools": {"required": False, "type": "array<string>"},
        "required-secrets": {"required": False, "type": "array<string>"},
    },
}


def load_contract() -> dict[str, Any]:
    """读取 skill 契约；缺失/损坏回退默认（不阻塞）。"""
    try:
        with open(_CONTRACT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else _DEFAULT_CONTRACT
    except (OSError, json.JSONDecodeError) as e:
        logger.debug(f"skill: 契约 {_CONTRACT_PATH} 不可用（{e}），用内置默认")
        return _DEFAULT_CONTRACT


def _split_frontmatter(text: str) -> tuple[Optional[str], str]:
    """`---` 包裹的前置块 → (frontmatter 文本, body)；无 frontmatter → (None, text)。

    用行锚定正则（`---` 独占一行才算分隔符），避免正文里的 `---`（如 markdown hr）
    干扰；frontmatter 值内部若含独立行 `---` 仍会误切（LOW 已知限制，fail-skip 兜底）。
    """
    parts = re.split(r"^---\s*$", text, 2, re.MULTILINE)
    if len(parts) < 3 or parts[0].strip():
        return None, text
    _fm, rest = parts[1], parts[2]
    body = rest[1:] if rest.startswith("\n") else rest
    return _fm, body


def _validate(data: dict[str, Any]) -> list[str]:
    """按契约校验 frontmatter，返回错误列表（空 = 合法）。"""
    errors: list[str] = []
    fields = load_contract().get("fields") or {}
    for key, spec in fields.items():
        if key not in data or data[key] in (None, ""):
            if spec.get("required"):
                errors.append(f"缺必填字段 {key}")
            continue
        val = data[key]
        t = spec.get("type")
        if t == "string":
            if not isinstance(val, str):
                errors.append(f"{key} 应为字符串")
            elif spec.get("max_chars") and len(val) > spec["max_chars"]:
                errors.append(f"{key} 超过 {spec['max_chars']} 字上限")
        elif t == "array<string>":
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                errors.append(f"{key} 应为字符串数组")
    return errors


def parse_skill_md(path: Path) -> Optional[tuple[dict[str, Any], str]]:
    """解析单个 SKILL.md → (frontmatter dict, body)；非法/契约不符 → None（调用方跳过）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")  # utf-8-sig 自动剥离 BOM（review LOW）
    except OSError as e:
        logger.warning(f"skill: 读取 {path} 失败: {e}")
        return None

    fm_text, body = _split_frontmatter(text)
    if fm_text is None:
        logger.warning(f"skill: {path} 缺 frontmatter，跳过")
        return None
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        logger.warning(f"skill: {path} frontmatter YAML 非法: {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"skill: {path} frontmatter 非映射，跳过")
        return None

    errors = _validate(data)
    if errors:
        logger.warning(f"skill: {path} 契约不符（{', '.join(errors)}），跳过")
        return None
    return data, body


__all__: list[str] = ["parse_skill_md", "load_contract"]
