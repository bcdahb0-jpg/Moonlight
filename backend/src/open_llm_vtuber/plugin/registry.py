"""plugin/registry.py — 插件目录扫描 + 索引（P5 插件生态）。

- 扫描 `backend/plugins/{builtin,community}/<name>/plugin.json`，建索引。
  my-neuro 同款 `category/name` 身份字符串（id = `builtin/echo`），
  避免 plugin_id 里带 `/` 被 URL 路由吞掉。
- plugin.json 字段：id 可选（默认 category/name）、name、version、author、
  description、hooks（list[str]）、entry（py 文件，可选）、readme（可选）。
- 只依赖 stdlib + loguru（单向依赖铁律，同 task_platform/skills）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# backend/（plugin/registry.py → parents[3] = backend/）
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGINS_ROOT = _BACKEND_ROOT / "plugins"
CATEGORIES = ("builtin", "community")
ENABLED_FILE = _BACKEND_ROOT / "data" / "plugins_enabled.json"


@dataclass
class PluginInfo:
    """单个插件索引项。path 为 plugin.json 所在目录。"""

    plugin_id: str  # "builtin/echo"
    category: str
    name: str
    title: str
    version: str
    author: str
    description: str
    hooks: list[str] = field(default_factory=list)
    entry: str = ""
    readme: str = ""
    enabled: bool = False
    path: Optional[Path] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "category": self.category,
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "hooks": list(self.hooks),
            "entry": self.entry,
            "readme": self.readme,
            "enabled": self.enabled,
        }


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"plugin: 读取 {path} 失败: {e}")
        return None


def load_enabled() -> set[str]:
    """读取已启用插件 id 集合；文件缺失 → 空集。"""
    data = _read_json(ENABLED_FILE)
    if not data:
        return set()
    items = data.get("plugins")
    if isinstance(items, list):
        return {str(i) for i in items}
    return set()


def save_enabled(enabled: set[str]) -> bool:
    """持久化启用集合到 data/plugins_enabled.json。"""
    try:
        ENABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENABLED_FILE.write_text(
            json.dumps({"plugins": sorted(enabled)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError as e:
        logger.error(f"plugin: 写入 {ENABLED_FILE} 失败: {e}")
        return False


def scan_plugins() -> list[PluginInfo]:
    """全量扫描 builtin + community 目录，返回插件索引（按 id 排序）。"""
    enabled = load_enabled()
    out: list[PluginInfo] = []
    for category in CATEGORIES:
        cat_dir = PLUGINS_ROOT / category
        if not cat_dir.is_dir():
            continue
        for entry in sorted(cat_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta = _read_json(entry / "plugin.json")
            if meta is None:
                continue  # 无合法 plugin.json → 跳过，绝不整库崩
            name = str(meta.get("name") or entry.name)
            plugin_id = f"{category}/{name}"
            info = PluginInfo(
                plugin_id=plugin_id,
                category=category,
                name=name,
                title=str(meta.get("title") or name),
                version=str(meta.get("version") or "0.1.0"),
                author=str(meta.get("author") or ""),
                description=str(meta.get("description") or ""),
                hooks=[str(h) for h in (meta.get("hooks") or [])],
                entry=str(meta.get("entry") or ""),
                readme=str(meta.get("readme") or ""),
                enabled=plugin_id in enabled,
                path=entry,
            )
            out.append(info)
    return sorted(out, key=lambda i: i.plugin_id)


def find_plugin(plugin_id: str) -> Optional[PluginInfo]:
    """按 `category/name` 查找插件；不存在 → None。"""
    for info in scan_plugins():
        if info.plugin_id == plugin_id:
            return info
    return None


__all__: list[str] = [
    "PluginInfo",
    "PLUGINS_ROOT",
    "CATEGORIES",
    "scan_plugins",
    "find_plugin",
    "load_enabled",
    "save_enabled",
]
