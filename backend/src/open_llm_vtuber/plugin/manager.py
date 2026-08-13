"""plugin/manager.py — 插件生命周期管理（P5 插件生态）。

- 单例 PluginManager：scan（registry）+ enabled 持久化 + 运行时表（懒加载）。
- `on_message(msg)` 分发：遍历已启用插件，首个返回非 None 的插件吞掉消息
  （返回其回复文本），否则返回 None（消息放行进主链路）。
- `on_load_all` / `on_unload`：启停时触发钩子。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from .hooks import PluginRuntime
from .registry import PluginInfo, load_enabled, save_enabled, scan_plugins


class PluginManager:
    """插件运行时管理器（线程安全由 GIL + 调用方保证，参考 emotion tracker）。"""

    def __init__(self) -> None:
        self._runtimes: dict[str, PluginRuntime] = {}
        self._rebuild()

    def _rebuild(self) -> None:
        """按目录重建运行时表（启用集变更后调用）。"""
        self._runtimes = {}
        for info in scan_plugins():
            if info.enabled:
                self._runtimes[info.plugin_id] = PluginRuntime(info)

    # ---- 查询 ----
    def list(self) -> list[PluginInfo]:
        return scan_plugins()

    def enabled_ids(self) -> list[str]:
        return sorted(self._runtimes.keys())

    def is_enabled(self, plugin_id: str) -> bool:
        return plugin_id in self._runtimes

    # ---- 启停 ----
    def toggle(self, plugin_id: str) -> dict:
        """切换插件启用状态；返回 {ok, enabled, error?}。"""
        info = next((i for i in scan_plugins() if i.plugin_id == plugin_id), None)
        if info is None:
            return {"ok": False, "error": f"插件不存在: {plugin_id}"}
        enabled = load_enabled()
        if plugin_id in enabled:
            # 停用：先跑 on_unload 再落盘
            runtime = self._runtimes.pop(plugin_id, None)
            if runtime is not None:
                runtime.call("on_unload")
            enabled.discard(plugin_id)
        else:
            # 启用：先落盘再重建（失败则回滚）
            enabled.add(plugin_id)
            if not save_enabled(enabled):
                return {"ok": False, "error": "写入启用状态失败"}
            runtime = PluginRuntime(info)
            runtime.call("on_load", {"manager": self})
            self._runtimes[plugin_id] = runtime
        save_enabled(enabled)
        return {"ok": True, "enabled": plugin_id in enabled, "plugin_id": plugin_id}

    # ---- 消息分发 ----
    def on_message(self, msg: dict) -> Optional[str]:
        """遍历已启用插件派发消息；首个吞掉的插件返回其回复，否则 None。"""
        for plugin_id in sorted(self._runtimes):
            runtime = self._runtimes[plugin_id]
            try:
                reply = runtime.call("on_message", msg)
            except Exception as e:  # noqa: BLE001
                logger.error(f"plugin: {plugin_id} on_message 异常: {e}")
                reply = None
            if reply is not None:
                logger.info(f"plugin: {plugin_id} 吞掉消息 → {str(reply)[:40]}")
                return str(reply)
        return None


_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """模块级单例（路由/测试共享同一实例）。"""
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager


__all__: list[str] = ["PluginManager", "get_plugin_manager"]
