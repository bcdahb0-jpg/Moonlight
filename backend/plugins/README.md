# Extensions 插件（plan §8 Phase 6c）

任务 agent 的 Python 钩子注册表（pi ExtensionAPI 子集），加载 `plugins/` 根下的插件模块。

## 目录约定（dwsy extensions 思想）

| 约定 | 说明 |
|---|---|
| `plugins/` | **启用**的插件根（唯一被扫描的目录） |
| `plugins/<name>.py` | 单文件插件；`plugins/<name>/__init__.py` 为包插件 |
| `.disabled` 后缀 | 根内文件/目录名带 `.disabled`（如 `foo.disabled.py`）→ 跳过，**零删除** |
| 目录改名 | 想整体禁用 → 把 `plugins/` 改名 `plugins.disabled/`（此时 root 不存在，扫描为空） |

## 插件契约

每个插件模块定义 `register(api: ExtensionAPI)`，在函数内用 `api.on(event, handler)` 注册钩子：

```python
def register(api):
    api.on("before_tool_call", my_before)   # 可 block / 改 args
    api.on("after_tool_call", my_after)     # 可改写工具返回
    api.on("before_agent_start", my_start)  # 可改写 system prompt
```

### 事件签名

- **before_agent_start**(system_prompt: str, ctx: dict) -> `str | None`
  返回新 prompt 覆盖；`None` 不修改。ctx = `{"task_id", "workspace"}`。
- **before_tool_call**(tool_name: str, args: dict, ctx: dict) -> `dict | None`
  返回 `{"block": True, "reason": "..."}` 拦截（生成 error ToolMessage 回环，不执行）；
  返回 `{"args": {...}}` 改写参数后执行；`None` 透传。ctx 含 `{"state"}`。
- **after_tool_call**(tool_name: str, result: str, ctx: dict) -> `dict | None`
  返回 `{"result": "..."}` 改写返回内容；`None` 保持原样。ctx 含 `{"state"}`。

钩子内任何异常被捕获并告警，**不会中断任务 run**（fail-soft）。

## 现有插件

- 无（默认空；参考示例见 `plugins.disabled/example_hooks.py`）。
