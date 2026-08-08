"""就绪度检查（domain 层，纯逻辑不碰 IO）。

Phase 3 的 Onboarding 向导会调用这些判定函数；当前仅作为分层规范的
首个落位示例。所有函数保持纯函数/依赖注入，IO（模型文件、网络探测）
由 application 层注入。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, Union

# 检查项标识（与 contracts/ws-protocol.md 的 readiness 消息一致）。
CHECK_ASR_MODEL = "asr_model"
CHECK_LLM = "llm"
CHECK_MIC = "mic"
CHECK_LIVE2D = "live2d"

# 判定函数类型：注入检查实现，返回 (是否通过, 用户可读提示)。
# 支持同步或异步（coroutine）实现。
Checker = Callable[[], Union[tuple[bool, str], Awaitable[tuple[bool, str]]]]


def check(checks: dict[str, Checker]) -> list[dict[str, Any]]:
    """运行一组同步就绪度检查，返回按声明顺序排列的结果列表。

    任何单项抛异常都降级为「未通过」，绝不中断整组检查。
    """
    results: list[dict[str, Any]] = []
    for item_id, checker in checks.items():
        try:
            result = checker()
            if asyncio.iscoroutine(result):
                raise TypeError(
                    "async checker passed to sync check(); use check_async instead"
                )
            passed, hint = result
        except Exception as exc:  # 防御：检查器自身故障不拖垮整体
            passed, hint = False, f"检查执行失败（{type(exc).__name__}）"
        results.append({"id": item_id, "passed": bool(passed), "hint": hint})
    return results


async def check_async(checks: dict[str, Checker]) -> list[dict[str, Any]]:
    """异步版 check：支持同步与协程检查器混用，逐项 await。"""
    results: list[dict[str, Any]] = []
    for item_id, checker in checks.items():
        try:
            result = checker()
            if asyncio.iscoroutine(result):
                result = await result
            passed, hint = result
        except Exception as exc:  # 防御：检查器自身故障不拖垮整体
            passed, hint = False, f"检查执行失败（{type(exc).__name__}）"
        results.append({"id": item_id, "passed": bool(passed), "hint": hint})
    return results


def all_passed(results: list[dict[str, Any]]) -> bool:
    """全部检查项通过才算就绪。"""
    return bool(results) and all(r["passed"] for r in results)


def to_message(results: list[dict[str, Any]]) -> dict[str, Any]:
    """打包为 WS 消息负载（type=ready），供 application 层发送。"""
    return {"type": "ready", "checks": results}
