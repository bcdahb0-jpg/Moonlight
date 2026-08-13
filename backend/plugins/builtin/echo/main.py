"""Echo 示例插件 — 演示 P5 插件契约（on_load/on_unload/on_message）。

消息以 `echo: ` 开头时吞掉并原样返回（大写化），否则返回 None 放行。
"""


def on_load(ctx):
    print(f"[echo-plugin] on_load, manager={ctx.get('manager') is not None}")


def on_unload():
    print("[echo-plugin] on_unload")


def on_message(msg):
    text = str(msg.get("text") or "")
    if text.startswith("echo: "):
        return text[6:].upper()
    return None
