"""plugin 包（P5 插件生态）。

分层：registry（扫描/索引）→ hooks（运行时钩子）→ manager（启停/分发）
→ marketplace（技能市场）→ export（导出导入脱敏）。
单向依赖：registry ← hooks ← manager；marketplace/export 只依赖 registry。
"""

from .registry import PluginInfo, find_plugin, scan_plugins  # noqa: F401
from .manager import PluginManager, get_plugin_manager  # noqa: F401
from .hooks import PluginRuntime  # noqa: F401
from .marketplace import catalog, install, install_status  # noqa: F401
from .export import scrub_secrets, export_character, export_config, import_config  # noqa: F401
from .intent import analyze_intent, intent_config  # noqa: F401
