"""示例插件（plan §8 6c）：演示全部 3 个钩子。默认停用（在 plugins.disabled/ 下）。

启用：把本文件复制到 `plugins/` 根（或把整个 `plugins.disabled/` 改名 `plugins/`）。
停用：改回 / 文件名加 `.disabled` 后缀。

演示：
- before_tool_call：拦截 `bash`（返回 block）。
- after_tool_call：把 `read_file` 的结果追加一行标记。
- before_agent_start：在 system prompt 末尾追加一行约束。
"""


def register(api):
    api.on("before_tool_call", _block_bash)
    api.on("after_tool_call", _mark_read_file)
    api.on("before_agent_start", _note_prompt)


def _block_bash(tool_name, args, ctx):
    if tool_name == "bash":
        return {"block": True, "reason": "示例插件禁止 bash，请改用专用工具"}
    return None


def _mark_read_file(tool_name, result, ctx):
    if tool_name == "read_file":
        return {"result": result + "\n[example_hooks] read_file 结果已标记"}
    return None


def _note_prompt(system_prompt, ctx):
    return system_prompt + "\n【example_hooks】read_file 返回内容可能带标记。"
