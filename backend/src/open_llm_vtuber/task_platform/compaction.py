"""上下文压缩（plan §6a / §5.4）：手动触发 checkpoint 消息压缩，长任务可续跑。

双写真相源（§4.2）：
1. **先 JSONL**：`SessionFile.add_compaction(...)`（审计 / UI 投影源，append-only）。
2. **再 checkpoint**：`aupdate_state` Overwrite(messages)（执行源，续跑据此恢复）。

任一步失败 → 本次压缩不生效并报错：摘要生成失败（模型异常）在**任何写入前**抛错；
checkpoint 写入失败 → 抛错，并在 JSONL 追加 `compaction_failed` 条目（审计可辨，非半成品成功记录）。

实现复用 `Summary` middleware 的摘要 / 安全切分逻辑（DRY，与自动压缩行为一致）：
- `_find_safe_cutoff` / `_partition_messages`：不拆散 AI/Tool 消息对；
- `_acreate_summary` / `_build_new_messages`：同摘要 prompt 生成压缩消息。

不 build 完整 agent（避免 MCP 子进程 + 技能扫描，review MEDIUM）：用 `create_agent`
最小图（同 `TaskState` + AsyncSqliteSaver，空 tools / 空 middleware）仅读/写 checkpoint
—— probe 已验证完整 `build_agent` 可续接压缩后的 checkpoint。
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import RemoveMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from . import graph, middleware, models
from .conf_bridge import TaskPlatformConfig
from .graph import checkpoint_db_path
from .session import SessionFile
from .state import TaskState

#: 默认保留的最近消息条数（与 Summary middleware 的 keep=("messages", 20) 一致）。
COMPACT_KEEP_MESSAGES = 20
#: v3 Phase 4：token 感知切点的默认保留 token 数（pi keepRecentTokens 思想）。
COMPACT_KEEP_TOKENS = 20000

#: `_acreate_summary` 吞掉模型异常后返回的错误摘要前缀（据此判定摘要生成失败）。
_SUMMARY_ERROR_PREFIX = "Error generating summary"


async def compact_task_context(
    task: models.Task,
    cfg: TaskPlatformConfig,
    *,
    model: Any | None = None,
    keep_messages: int = COMPACT_KEEP_MESSAGES,
    keep_tokens: int | None = None,
    bus: Any | None = None,
) -> dict[str, Any]:
    """压缩任务 checkpoint 的消息历史（旧消息摘要化，保留最近 N 条/token 预算）。

    v3 Phase 4：默认改用 **token 感知切点**（pi `findCutPoint` 思想，keep_tokens=20k），
    切点永不落在 ToolMessage 上（AI/Tool 对不拆散）；`keep_tokens=0` 时回退消息数切点。

    Args:
        task: 目标任务（workspace/id 决定 checkpoint 与 session 落点）。
        cfg: task_platform 配置。
        model: 摘要生成模型（默认 graph.build_model(cfg)，测试注入 stub）。
        keep_messages: 保留的最近消息条数（keep_tokens=0 时生效；消息数 ≤ 此值时 no-op）。
        keep_tokens: token 感知切点的保留 token 数（默认 COMPACT_KEEP_TOKENS；0=禁用）。
        bus: 事件总线（可选；压缩成功后双落一条 `status/compacted` 审计事件）。

    Returns:
        compacted=True 时：{compacted, summary, removed, kept, entry_id, message_count}；
        no-op 时：{compacted=False, reason, message_count}。

    Raises:
        RuntimeError: 摘要生成失败或 checkpoint 写入失败（不产生半成品状态）。
    """
    model = model or graph.build_model(cfg)
    summary_mw = middleware.Summary(model)

    config = {"configurable": {"thread_id": task.id}}
    db = checkpoint_db_path(task.workspace, task.id)
    db.parent.mkdir(parents=True, exist_ok=True)  # 任务无历史时目录可能不存在
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        # 最小图（空 tools / 空 middleware）：仅读/写 checkpoint，不加载 MCP/技能子进程。
        agent = create_agent(
            model=model,
            tools=[],
            state_schema=TaskState,
            middleware=[],
            checkpointer=saver,
        )
        snap = await agent.aget_state(config)
        messages = list((snap.values or {}).get("messages") or [])

        # v3 Phase 4：token 感知切点优先（不拆 AI/Tool 对）；keep_tokens=0 → 消息数切点。
        tokens = keep_tokens if keep_tokens is not None else COMPACT_KEEP_TOKENS
        if tokens > 0 and len(messages) > COMPACT_KEEP_MESSAGES:
            cutoff = summary_mw.token_aware_cutoff(messages, tokens)
        else:
            cutoff = summary_mw._find_safe_cutoff(messages, keep_messages)
        if cutoff <= 0:
            return {"compacted": False, "reason": "below_threshold", "message_count": len(messages)}

        to_summarize, preserved = summary_mw._partition_messages(messages, cutoff)
        summary = await summary_mw._acreate_summary(to_summarize)
        if summary.startswith(_SUMMARY_ERROR_PREFIX):
            # 模型摘要失败：在任何写入前抛错，压缩不生效。
            raise RuntimeError(f"上下文压缩失败：摘要生成异常（{summary}）")
        new_messages = summary_mw._build_new_messages(summary)

        # ---- 双写真相源（§4.2）：先 JSONL 审计，后 checkpoint 执行源 ----
        # firstKeptEntryId 锚定保留区首条消息的 LangGraph 消息 id（checkpoint id 空间）。
        first_kept_id = str(getattr(preserved[0], "id", "") or "") if preserved else ""
        session = SessionFile(task.workspace, task.id)
        session.ensure()
        entry_id = session.add_compaction(summary, first_kept_id)
        try:
            # as_node="__start__"：最小图里 messages 是 model/tools 多节点输入，缺省归属会
            # 报 Ambiguous update（尤其 checkpoint 曾由 aupdate_state 写入时）。
            await agent.aupdate_state(
                config,
                {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages, *preserved]},
                as_node="__start__",
            )
        except Exception as e:
            # 审计真相源：JSONL 里失败显式可辨（compaction_failed 条目），非半成品成功记录。
            session.add_compaction_failed(entry_id, str(e))
            raise RuntimeError(
                f"上下文压缩失败：checkpoint 写入失败"
                f"（JSONL 已记录 compaction entry {entry_id} + 失败标记）：{e}"
            ) from e

    if bus is not None:
        bus.emit_status("compacted", f"上下文压缩：移除 {len(to_summarize)} 条，保留 {len(preserved)} 条")

    return {
        "compacted": True,
        "summary": summary,
        "removed": len(to_summarize),
        "kept": len(preserved),
        "entry_id": entry_id,
        "message_count": len(messages),
    }
