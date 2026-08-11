"""项目级记忆 DeerMem 包（v3 Phase 7）。

用法：
    from .store import get_memory_manager
    mgr = get_memory_manager(workspace)
    mgr.add(title="...", content="...", category="...", task_id="...")
    ctx = mgr.get_context(query, max_tokens=1500)   # 注入 system prompt 的纯文本
"""

from .store import MemoryStore, get_memory_manager

__all__ = ["MemoryStore", "get_memory_manager"]
