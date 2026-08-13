"""plugin/hooks.py — 插件生命周期钩子契约（P5 插件生态）。

v1 契约（同进程 importlib 动态加载，N.E.K.O 多进程 ZMQ 方案列为 v2）：
- `on_load(ctx)`：插件启动回调，ctx 为 dict（含 manager 单例引用）。
- `on_unload()`：插件卸载/禁用回调。
- `on_message(msg)`：收到对话/弹幕消息时触发，msg 为 dict；
  返回 Optional[str] 作为「吞掉消息」的替代回复（return None 则放行）。

entry 约定：plugin.json 的 `entry` 指向插件目录内一个 .py 文件
（默认 main.py）。模块顶层若无对应钩子函数，对应生命周期静默跳过
（fail-soft，插件写得烂不拖垮主程序）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .registry import PluginInfo


def _load_module(plugin_dir: Path, entry: str) -> Optional[Any]:
    """按 entry 加载插件模块；失败 → None（绝不抛到上层）。"""
    if not entry:
        entry = "main.py"
    entry_path = plugin_dir / entry
    if not entry_path.is_file():
        logger.warning(f"plugin: {plugin_dir.name} entry {entry} 不存在")
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            f"moonlight_plugin_{plugin_dir.name}", entry_path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # noqa: BLE001 — 插件代码不可信，任何异常都兜住
        logger.error(f"plugin: 加载 {plugin_dir.name} 失败: {e}")
        return None


class PluginRuntime:
    """单个插件的运行时句柄（懒加载模块 + 钩子分发）。"""

    def __init__(self, info: PluginInfo) -> None:
        self.info = info
        self._module: Optional[Any] = None
        self._loaded = False

    @property
    def module(self) -> Optional[Any]:
        if not self._loaded:
            self._loaded = True
            if self.info.path is not None:
                self._module = _load_module(self.info.path, self.info.entry)
        return self._module

    def call(self, hook: str, *args: Any, **kwargs: Any) -> Any:
        """调用钩子函数；模块/函数缺失 → 返回 None（fail-soft）。"""
        module = self.module
        if module is None:
            return None
        fn = getattr(module, hook, None)
        if not callable(fn):
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — 插件异常不得外泄
            logger.error(f"plugin: {self.info.plugin_id} hook {hook} 异常: {e}")
            return None


__all__: list[str] = ["PluginRuntime"]
