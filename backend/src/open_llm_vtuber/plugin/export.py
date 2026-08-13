"""plugin/export.py — 角色卡/配置导出导入（P5 插件生态，mea-pet store.py 思路）。

- `scrub_secrets(data)`：深拷贝，把敏感 key（api_key/auth_token/token/
  sessdata/bili_jct/buvid3/password）清空为 ""，**保留字段形状**
  （读取方不因缺键失败）。递归处理 dict/list。
- `export_character()`：角色卡（character_config）+ 玩家提示词 + LLM 节，
  脱敏后返回。
- `export_config()`：全部 conf 脱敏。
- `import_config(payload)`：校验 schema → 与现有 conf merge（去重）→
  仅写入白名单节（surgical leaf，绝不覆盖敏感字段）。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

from loguru import logger

#: 脱敏 key 模式（递归匹配 dict 键名；token 类仅整键/后缀匹配，避免误伤 token_count）。
_SECRET_KEYS = re.compile(
    r"(^|_)(api_?key|auth_?token|access_?token|refresh_?token|token|secret|password|session)$|"
    r"sessdata|bili_jct|buvid3|credential|cookie",  # noqa: E501
    re.IGNORECASE,
)

#: 导入白名单节（仅这些顶层块可被 import merge）。
_IMPORTABLE_BLOCKS = (
    "character_config",
    "player_config",
    "system_config",
)

#: system_config 内白名单（其余子节不导入，避免覆盖运行配置）。
_IMPORTABLE_SYSTEM_SUB = ("expression", "singing", "live", "playmate", "intent", "plugin")


def scrub_secrets(data: Any) -> Any:
    """深拷贝 + 清空敏感字段值（保留形状）。"""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if _SECRET_KEYS.search(str(k)):
                out[k] = "" if not isinstance(v, (dict, list)) else scrub_secrets(v)
            else:
                out[k] = scrub_secrets(v)
        return out
    if isinstance(data, list):
        return [scrub_secrets(i) for i in data]
    return data


def _read_conf() -> dict:
    try:
        from ..config_manager.utils import read_yaml  # noqa: PLC0415

        return read_yaml("conf.yaml") or {}
    except Exception:
        return {}


def export_character() -> dict:
    """角色卡导出：character_config + 玩家提示词 + LLM 节（脱敏）。"""
    conf = _read_conf()
    payload = {
        "version": 1,
        "kind": "character",
        "exported_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "character_config": conf.get("character_config") or {},
        "player_config": conf.get("player_config") or {},
    }
    return scrub_secrets(payload)


def export_config() -> dict:
    """全配置导出（脱敏）。"""
    conf = _read_conf()
    payload = {
        "version": 1,
        "kind": "config",
        "exported_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "config": scrub_secrets(conf),
    }
    return payload


def import_config(payload: Any) -> dict:
    """导入配置：校验 schema → merge → 白名单 surgical 写入。

    返回 {ok, imported: [key...], skipped: [key...], error?}。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload 非 JSON 对象"}
    kind = payload.get("kind")
    if kind not in ("character", "config"):
        return {"ok": False, "error": f"未知 kind: {kind!r}"}

    conf = _read_conf()
    imported: list[str] = []
    skipped: list[str] = []

    if kind == "character":
        merged = {
            "character_config": payload.get("character_config") or {},
            "player_config": payload.get("player_config") or {},
        }
        for block, data in merged.items():
            if isinstance(data, dict) and data:
                conf[block] = _merge_dict(conf.get(block) or {}, data)
                imported.append(block)
            else:
                skipped.append(block)
    else:
        src = payload.get("config")
        if not isinstance(src, dict):
            return {"ok": False, "error": "config 块缺失"}
        for block in _IMPORTABLE_BLOCKS:
            data = src.get(block)
            if not isinstance(data, dict) or not data:
                skipped.append(block)
                continue
            if block == "system_config":
                merged_sub: dict[str, Any] = {}
                for sub in _IMPORTABLE_SYSTEM_SUB:
                    sub_data = data.get(sub)
                    if isinstance(sub_data, dict) and sub_data:
                        merged_sub[sub] = _merge_dict(
                            (conf.get("system_config") or {}).get(sub) or {}, sub_data
                        )
                if merged_sub:
                    sc = conf.setdefault("system_config", {})
                    sc.update(merged_sub)
                    imported.append("system_config")
            else:
                conf[block] = _merge_dict(conf.get(block) or {}, data)
                imported.append(block)

    ok = _write_conf(conf)
    if not ok:
        return {"ok": False, "error": "写入 conf.yaml 失败"}
    return {"ok": True, "imported": imported, "skipped": skipped}


def _merge_dict(base: dict, incoming: dict) -> dict:
    """浅层 merge：incoming 的非空值覆盖 base 同键；空值保留 base。"""
    out = copy.deepcopy(base)
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        elif v not in (None, "", [], {}):
            out[k] = copy.deepcopy(v)
    return out


def _write_conf(conf: dict) -> bool:
    try:
        import yaml  # noqa: PLC0415

        with open("conf.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(conf, f, allow_unicode=True, sort_keys=False)
        return True
    except Exception as e:
        logger.error(f"plugin/export: 写 conf 失败: {e}")
        return False


__all__: list[str] = ["scrub_secrets", "export_character", "export_config", "import_config"]
