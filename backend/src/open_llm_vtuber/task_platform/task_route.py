"""任务平台 REST 路由（plan §6 Phase 1 子集 + Phase 2 run/SSE/interrupt）。

- GET    /api/tasks                 任务列表
- POST   /api/tasks                 新建 {title, goal?, workspace?}
- GET    /api/tasks/{id}            详情 + 会话历史（JSONL 投影）
- PATCH  /api/tasks/{id}            改 title/goal/status
- DELETE /api/tasks/{id}            删任务（不删工作目录）
- GET    /api/workspaces/scan       目录扫描（深度 2，新建任务对话框用）
- GET    /api/workspaces/root       默认任务根路径

Phase 2 追加：
- POST /api/tasks/{id}/runs         发起执行 {message?}（后台跑 agent，SSE 经 EventBus 广播）
- GET  /api/tasks/{id}/runs/stream  SSE 事件流（先回放已落库 seq>N，再实时；断线重连锚点）
- POST /api/tasks/{id}/interrupt    打断运行（取消 agent 任务 → run_end(interrupted)）

Phase 3：/skills；Phase 6：/compact。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger
from starlette.responses import JSONResponse, StreamingResponse

from . import compaction, goal, graph, hooks, models
from .conf_bridge import TaskPlatformConfig, task_config
from .memory.store import get_memory_manager
from .session import SessionFile

# 从现有 llm_config_route 复用本地请求守卫（localhost + Tailscale + allow_remote_config）。
from ..llm_config_route import _is_local_request, _forbidden

#: 每任务常驻 EventBus（跨 run 复用；run_id 随新 run 更新）。SSE 断线重连以此为实时源。
_buses: dict[str, hooks.EventBus] = {}
#: 每任务当前 run 上下文（含后台 agent 任务，供 interrupt）。
_runs: dict[str, "RunContext"] = {}

#: 外壳播报广播函数（server.py 注入：把 WS audio payload 发给所有已连接前端）。
#: None = 未注入（离线/测试环境）→ shell.speak 直接跳过，不影响任务链路。
_SSE_HEARTBEAT_SEC = 15
#: /api/tasks/tools 探测结果缓存 TTL（探测会启动子进程，避免每次请求都重探测 —— review MEDIUM）。
_TOOLS_CACHE_TTL_SEC = 60
_tools_cache: tuple[float, dict[str, Any]] | None = None


class RunBusyError(RuntimeError):
    """同一任务已有运行中的 run。"""


class RunContext:
    """一次 run 的运行时状态：元数据 + 常驻 bus + 后台 agent 任务。"""

    def __init__(
        self,
        task: models.Task,
        run: models.Run,
        bus: hooks.EventBus,
        cfg: TaskPlatformConfig,
        persist_chat_message: bool = True,
    ):
        self.task = task
        self.run = run
        self.bus = bus
        self.cfg = cfg
        self.persist_chat_message = persist_chat_message
        self.run_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self.run_task is not None and not self.run_task.done()


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _default_tasks_root() -> Path:
    """默认任务根（首次运行自动创建）。"""
    root = task_config().tasks_root_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_workspace(raw: str | None) -> tuple[str, bool]:
    """返回 (workspace 绝对路径 realpath, 是否为自动创建)。

    - 未指定 → 默认根下建 `task-<ts>/`。
    - 已指定 → 目录不存在则创建（容错）。
    """
    if raw:
        p = Path(raw).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return str(p.resolve()), False
    root = _default_tasks_root()
    p = root / f"task-{_next_auto_id()}"
    p.mkdir(parents=True, exist_ok=True)
    return str(p.resolve()), True


def _next_auto_id() -> str:
    import time

    return time.strftime("%Y%m%d%H%M%S")


def _task_public(task: models.Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "workspace": task.workspace,
        "goal": task.goal,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "last_run_id": task.last_run_id,
        "conversation_uid": task.conversation_uid,
    }


# --------------------------------------------------------------------------- #
# Run 编排（Phase 2）：后台跑 agent + SSE 事件流 + interrupt
# --------------------------------------------------------------------------- #
def _discard_task_state(task_id: str) -> None:
    """任务删除时清理内存注册表：取消仍在跑的 run，丢弃常驻 bus。"""
    ctx = _runs.pop(task_id, None)
    if ctx is not None and ctx.run_task is not None and not ctx.run_task.done():
        ctx.run_task.cancel()
    _buses.pop(task_id, None)


def _bus_for(task: models.Task, run_id: str) -> hooks.EventBus:
    """取/建任务常驻 EventBus；新 run 复用实例并更新 run_id（事件按 task 续 seq）。"""
    bus = _buses.get(task.id)
    if bus is None:
        bus = hooks.EventBus(task.workspace, task.id, run_id)
        _buses[task.id] = bus
    else:
        bus.run_id = run_id
    return bus


def _budget_hard_stopped(state: dict[str, Any]) -> bool:
    """检测 token_budget HARD STOP：middleware 注入的「上下文已满」系统消息。

    v6.5：HARD STOP 是 `jump_to=end` 的正常返回，`_run_task_agent` 若不知情
    会把 run 标成 completed（假完成）——用户看到"任务完成了"但实际第一步就死。
    检测到后由调用方转 run_error。
    """
    for m in state.get("messages") or []:
        if isinstance(m, HumanMessage) and "上下文已满" in str(m.content or ""):
            return True
    return False


async def _run_task_agent(
    ctx: RunContext,
    message: str,
    *,
    model: Any | None = None,
    extra_tools: tuple = (),
    goal_evaluator: Any | None = None,
) -> None:
    """后台执行一次 run：Goal 状态机驱动 agent 多轮，经 EventBus 广播事件，终态落 run 表。

    Goal 状态机（plan §5.2 D）：
    - 每轮 ainvoke 后用主模型评估目标是否达成（fail-soft：解析失败视为已完成）。
    - 未达成且可推进 → 注入隐藏 continuation（SystemMessage + hide_from_ui=True）续跑。
    - 连续 max_no_progress 轮输出相同 → 提示用户澄清并结束（无死循环）。
    - 空目标 → 单轮执行（旧行为）。
    - 取消（interrupt）→ CancelledError → run_end(status=interrupted)。
    - 异常 → run_error 事件 + run 状态 error（不阻断后续 run）。
    """
    bus, task = ctx.bus, ctx.task
    cfg = ctx.cfg
    evaluator = goal_evaluator or goal.evaluate_goal_completion

    try:
        run_summary = ""  # 最后一条 AI 文本（run_end 播报/简报用）
        # 2026-08-10：run_start 携带本次触发指令 + 任务执行序号（前端据此渲染
        # 「第 N 次执行 · 本次指令」摘要，续跑时不再永远显示首次创建的任务标题）。
        run_number = 0
        try:
            run_number = len(models.list_runs(task.id))
        except Exception:
            pass
        bus.emit_run_start(task.goal, message=message, run_number=run_number)
        bus.emit_message("user", message)
        bus.session.add_message({"role": "user", "content": message})
        # 聊天委托的 message 是模型生成的内部 goal，不是用户在聊天区发送
        # 的原文；它只属于任务 session/EventBus。任务模式手动运行则保留
        # 用户输入投影，方便该模式刷新后恢复。
        if ctx.persist_chat_message:
            _inject_chat_message(task, "user", message)
        model = model or graph.build_model(cfg)
        async with graph.build_agent(
            task.workspace, task.id, bus=bus, cfg=cfg, model=model, extra_tools=extra_tools,
            goal=task.goal,
        ) as agent:
            iteration = 0
            no_progress = 0
            last_sig = ""
            state_input: dict[str, Any] = {
                "messages": [HumanMessage(content=message)],
                "goal": task.goal,
                "workspace": task.workspace,
            }
            while True:
                iteration += 1
                state = await agent.ainvoke(
                    state_input,
                    {"configurable": {"thread_id": task.id}},
                )
                # v6.5：token_budget HARD STOP（上下文超预算被硬停）→ run_error，
                # 不是 completed。避免「假完成」：用户看到任务成功但实际第一步就死。
                if _budget_hard_stopped(state):
                    err_msg = "上下文已满（token 预算耗尽），任务未完成。请执行 /compact 压缩历史后重试。"
                    logger.warning(f"run {ctx.run.id} hard-stopped by token_budget")
                    bus.emit_run_error(err_msg)
                    _finish_run(ctx.run.id, "error", error=err_msg)
                    return
                # 最后一条 AI 文本只属于任务卡/任务事件；不能写入普通聊天历史。
                # 委托任务的 agent 输出不是用户可见的聊天回复，真正的对话回复
                # 会由 basic_memory_agent 在收到 tool result 后负责落盘。
                for m in reversed(state["messages"]):
                    if isinstance(m, AIMessage) and m.content:
                        text = str(m.content)
                        run_summary = text
                        bus.emit_message("assistant", text)
                        bus.session.add_message({"role": "assistant", "content": text})
                        break
                # 空目标 → 单轮完成（不做目标评估）
                if not task.goal.strip():
                    break
                ev = await evaluator(model, task.goal, state["messages"])
                if ev.satisfied:
                    break
                # 无进展检测：同 AI 文本连续 max_no_progress 轮 → 提示澄清并结束
                # （先于迭代上限判断，保证「无进展 N 轮 → 澄清」语义，见 goal.py）
                sig = goal.recent_text_signature(state["messages"])
                if sig and sig == last_sig:
                    no_progress += 1
                else:
                    no_progress = 1
                last_sig = sig
                if no_progress >= cfg.max_no_progress:
                    # review LOW：发 clarify_requested 事件，前端可区分「需澄清」与「正常完成」
                    bus.emit_clarify(goal.CLARIFICATION_MESSAGE)
                    bus.emit_message("assistant", goal.CLARIFICATION_MESSAGE)
                    bus.session.add_message({"role": "assistant", "content": goal.CLARIFICATION_MESSAGE})
                    break
                if not goal.should_continue(ev, iteration=iteration, max_iterations=cfg.max_iterations):
                    break
                # 隐藏 continuation：追加 SystemMessage（hide_from_ui=True），checkpointer 续跑
                state_input = {
                    "messages": [SystemMessage(
                        content=goal.CONTINUATION_PROMPT,
                        additional_kwargs={"hide_from_ui": True},
                    )],
                }
        artifacts = _collect_task_artifacts(task.id, task.workspace)
        report = _build_task_report(task, run_summary, artifacts)
        # 2026-08-10：run_end 播报携带任务摘要——LLM 转述据此汇报真实结果
        # （"番茄钟做好了，pomodoro.html 在工作目录"），而非干巴巴的"任务完成了"。
        # v3 Phase 7：任务完成 → 写入项目级记忆（DeerMem；失败静默不影响主链路）。
        try:
            _write_task_memory(task, state.get("messages") or [])
        except Exception as _m:
            logger.debug(f"[memory] 任务记忆写入失败（静默）：{_m}")
        # P2 上下文桥：任务简报回注发起会话（角色"记得"任务，可自然续聊）。
        # 2026-08-10 修复「简报刷屏历史会话」：
        #   1. 取最后一条 AI 总结（run_summary，收尾发言）而非"最长 AI 文本"
        #      —— 长的是中途技术独白（BoxGeometry/坐标等），不适合回注会话；
        #   2. 截断 500 字符（AI 完整回复已通过 message 事件存在于会话历史，
        #      简报只补充结构化信息：任务标题 + 摘要 + 工作目录 + 生成文件）；
        #   3. 按 task_id upsert —— 同一任务多次 run 只保留最新一条简报，
        #      不再每次 run 都追加一条导致历史被任务记录淹没。
        try:
            # Chat delegation already has its final answer persisted by the
            # normal conversation pipeline.  A second task_brief would be a
            # duplicate card that reappears after refresh.  Keep the brief for
            # the standalone task mode only.
            if ctx.persist_chat_message and run_summary:
                _inject_task_brief(task, run_summary[:500], artifacts=artifacts)
        except Exception as _b:
            logger.debug(f"[shell] 简报生成失败（静默）：{_b}")
        # 任务报告只通过 task card / task_result 展示，不再重复写入普通聊天历史。
        # 所有历史投影完成后再广播终态，避免用户在收到完成事件后立即刷新，
        # 却遇到“当前界面有、历史里没有”的读写竞态。
        bus.emit_run_end(
            "completed",
            summary=(run_summary or "")[:800] or None,
            report=report,
            artifacts=artifacts,
        )
        # delegate-task must observe completion only after the brief is written.
        # Otherwise the chat request can finish first and a later history write
        # makes the brief appear only after refresh (or overwrite other messages).
        _finish_run(ctx.run.id, "completed")
    except asyncio.CancelledError:
        bus.emit_run_end("interrupted")
        _finish_run(ctx.run.id, "interrupted")
        raise
    except Exception as e:  # 运行失败不阻断后续 run
        logger.exception(f"run {ctx.run.id} failed: {e}")
        bus.emit_run_error(str(e))
        _finish_run(ctx.run.id, "error", error=str(e))
    # 不把 ctx.run_task 置 None：is_running 已用 run_task.done() 判定，保留引用供调用方 await。


def _finish_run(run_id: str, status: str, *, error: str | None = None) -> None:
    """落 run 终态；DB 写失败仅记录，不阻断 run 收尾（review MEDIUM）。"""
    try:
        models.finish_run(run_id, status, error=error)
    except Exception as e:
        logger.error(f"finish_run failed run={run_id} status={status}: {e}")


_WRITE_TOOLS = ("write_file", "str_replace", "browser_screenshot")
"""产物收集关注的写文件工具（sandbox 工具集 + browser 截图）。"""


def _collect_task_artifacts(task_id: str, workspace: str, limit: int = 8) -> list[str]:
    """从 run 事件里收集任务生成的产物文件绝对路径（2026-08-10）。

    依据：`tool_call` 事件（name + arguments.path）配对 `tool_result` 事件
    （is_error=False）确定**成功写盘**的文件；路径按工作目录解析后只保留
    真实存在的文件，去重、截断到 limit 条。全程 fail-soft：解析失败返回空。
    """
    if not workspace:
        return []
    ws = Path(workspace).resolve()
    written: dict[str, str] = {}  # tool_call_id -> 相对/绝对 path
    failed: set[str] = set()      # is_error=True 的 tool_call_id
    try:
        for ev in models.events_after(task_id, 0):
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                continue
            if ev.event_type == "tool_call":
                name = str(payload.get("name") or "")
                if name not in _WRITE_TOOLS:
                    continue
                args = payload.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                path = str((args or {}).get("path") or "").strip()
                if path:
                    written[str(payload.get("tool_call_id") or "")] = path
            elif ev.event_type == "tool_result":
                if payload.get("is_error"):
                    failed.add(str(payload.get("tool_call_id") or ""))
    except Exception as e:
        logger.debug(f"[artifacts] 事件读取失败（静默）：{e}")
        return []

    artifacts: list[str] = []
    seen: set[str] = set()
    for call_id, raw in written.items():
        if call_id in failed:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = ws / p
        try:
            p = p.resolve()
        except Exception:
            continue
        if p.is_file() and str(p) not in seen:
            seen.add(str(p))
            artifacts.append(str(p))
        if len(artifacts) >= limit:
            break
    return artifacts


def _chat_ctx(task: models.Task) -> tuple[str, str] | None:
    """解析任务发起会话的 (conf_uid, character_name)（conf.yaml character_config 块）。

    - 返回 None = 配置缺失/解析失败（调用方静默跳过，不阻断 run）。
    - 2026-08-10：抽出公共函数——简报回注与对话消息回注共用同一套 conf 解析。
    """
    try:
        import yaml

        from .conf_bridge import CONF_PATH

        conf_path = Path(CONF_PATH)
        if not conf_path.exists():
            return None
        data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        cc = data.get("character_config") or {}
        conf_uid = str(cc.get("conf_uid") or cc.get("conf_name") or "").strip()
        if not conf_uid:
            return None
        # conf_name is the selected character-card name used by chat bubbles.
        # Do not use legacy character_name here (hiyori may still contain 小月).
        char_name = str(cc.get("conf_name") or "AI")
        return conf_uid, char_name
    except Exception:
        return None


def _inject_chat_message(
    task: models.Task,
    role: str,
    content: str,
    *,
    kind: str | None = None,
) -> None:
    """2026-08-10：任务对话消息回注发起会话的聊天历史（P2 上下文桥补充）。

    - 背景：任务平台的消息此前只写 task session（JSONL），**从不进 chat_history**——
      重启后加载历史会话只剩简报，对话全丢（用户反馈"历史对话没有了"）。
    - 本函数在 user/AI 消息 emit 时同步写入 chat_history（role=human/ai, kind=None），
      历史会话据此恢复完整对话；简报（upsert 卡片）保留在消息之后。
    - 全程 fail-soft：会话 uid 缺失 / 配置缺失 / 写入失败均静默，不阻断 run。
    """
    conv_uid = (task.conversation_uid or "").strip()
    if not conv_uid:
        return
    text = (content or "").strip()
    if not text:
        return
    try:
        from ..chat_history_manager import store_message

        ctx = _chat_ctx(task)
        if ctx is None:
            return
        conf_uid, char_name = ctx
        store_kwargs = dict(
            conf_uid=conf_uid,
            history_uid=conv_uid,
            role="human" if role == "user" else "ai",
            content=text,
            name=char_name if role != "user" else None,
        )
        # task_report 是唯一需要在刷新后作为普通角色气泡保留的任务消息；
        # task_brief/task_shell 仍由前端折叠或隐藏，避免重复。
        if kind:
            store_kwargs["kind"] = kind
            store_kwargs["task_id"] = task.id
        store_message(**store_kwargs)
    except Exception as e:  # fail-soft
        logger.debug(f"[shell] 对话消息回注失败（静默）：{e}")


def _build_task_report(task: models.Task, summary: str, artifacts: list[str]) -> str:
    """生成一条简短的角色汇报，不复制任务卡的完整输出。"""
    result = " ".join((summary or "").split()).strip()
    if len(result) > 220:
        result = result[:220].rstrip("，。；; ") + "……"
    parts = ["任务完成啦"]
    if result:
        parts.append(result)
    if artifacts:
        names = "、".join(Path(p).name for p in artifacts[:3])
        parts.append(f"生成文件：{names}")
    parts.append("详细过程和结果都在上面的任务卡里。")
    return "，".join(parts[:2]) + ("。" if len(parts) == 2 else "；" + "；".join(parts[2:]) + "")


def _inject_task_brief(task: models.Task, summary: str, artifacts: list[str] | None = None) -> None:
    """run_end(completed) 时把任务简报写入发起会话的聊天历史（P2 上下文桥）。

    - 会话级注入：只写当前会话的 chat_history（role=ai），**不写四层记忆**
      （core/FTS/vector/memory_v2，§5.8 记忆边界），后续聊天加载历史即"记得"任务。
    - 依赖 conf_uid + character_name（conf.yaml character_config 块）。
    - 2026-08-10：简报追加「工作目录」与「生成文件」行——用户问"文件在哪"
      类追问时 LLM 能直接依据简报回答，不再重复委派任务。
    - 2026-08-10：改用 upsert_task_brief（kind="task_brief" + task_id 去重）——
      同一任务多次 run 只保留最新一条，不再每次追加；前端据此渲染折叠卡片。
    - 全程 fail-soft：会话 uid 缺失 / 历史文件不存在 / 写入失败均静默，不阻断 run。
    """
    conv_uid = (task.conversation_uid or "").strip()
    if not conv_uid:
        return
    text = (summary or "").strip()
    if not text:
        return
    try:
        from ..chat_history_manager import upsert_task_brief

        ctx = _chat_ctx(task)
        if ctx is None:
            return
        conf_uid, char_name = ctx
        brief = f"【任务简报】{task.title}：{text}"
        brief += f"\n工作目录：{task.workspace}"
        if artifacts:
            brief += "\n生成文件：" + "、".join(artifacts)
        upsert_task_brief(
            conf_uid=conf_uid,
            history_uid=conv_uid,
            task_id=task.id,
            content=brief,
            name=char_name,
        )
    except Exception as e:  # fail-soft：简报失败静默，不阻断 run
        logger.debug(f"[shell] 任务简报写入失败（静默）：{e}")


def _write_task_memory(task: models.Task, messages: list[Any]) -> None:
    """v3 Phase 7：任务完成 → 写入项目级记忆（DeerMem，跨任务积累）。

    - 提取最后一条完整 AI 文本（任务结果）+ 目标 + 工具调用轨迹摘要。
    - 落点：<workspace>/.pi/memory/facts/（跟随工作目录，同生命周期）。
    - 全程 fail-soft：任何失败静默（已由调用方 try 包裹，此处仅二次防御）。
    """
    goal_text = (task.goal or "").strip()
    result_text = ""
    tool_trace: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage):
            c = str(m.content or "").strip()
            if len(c) > len(result_text):
                result_text = c
            for tc in m.tool_calls or []:
                name = str(tc.get("name") or "")
                if name not in ("describe_skill", "read_skill"):
                    tool_trace.append(name)
    if not result_text and not goal_text:
        return
    mgr = get_memory_manager(task.workspace)
    title = goal_text[:60] or f"任务 {task.id[:8]}"
    body = f"目标：{goal_text}\n结果：{result_text[:800]}"
    if tool_trace:
        # 去重保序的工具轨迹（模型能看到"这任务用过哪些工具"）
        seen: list[str] = []
        for t in tool_trace:
            if t not in seen:
                seen.append(t)
        body += f"\n工具轨迹：{', '.join(seen[:20])}"
    mgr.add(
        title=title,
        content=body,
        category="task",
        task_id=task.id,
        confidence=0.7,
    )


def start_run(
    task: models.Task,
    cfg: TaskPlatformConfig,
    message: str,
    *,
    model: Any | None = None,
    extra_tools: tuple = (),
    goal_evaluator: Any | None = None,
    persist_chat_message: bool = True,
) -> RunContext:
    """发起一次 run：建 run 行 + 取/建 bus + 起后台 agent 任务。返回 RunContext。

    - 同任务已有运行中 run → RunBusyError。
    - 须在运行中的事件循环内调用（POST 处理器天然满足；测试包 asyncio.run）。
    """
    prev = _runs.get(task.id)
    if prev is not None and prev.is_running:
        raise RunBusyError(f"task {task.id} 已有运行中的 run")

    run = models.create_run(task.id)
    bus = _bus_for(task, run.id)
    ctx = RunContext(
        task=task,
        run=run,
        bus=bus,
        cfg=cfg,
        persist_chat_message=persist_chat_message,
    )
    ctx.run_task = asyncio.create_task(
        _run_task_agent(ctx, message, model=model, extra_tools=extra_tools, goal_evaluator=goal_evaluator)
    )
    _runs[task.id] = ctx
    logger.info(f"run started: task={task.id} run={run.id} msg={message[:60]!r}")
    return ctx


def _sse_event(ev: models.TaskEvent) -> str:
    return f"data: {ev.model_dump_json()}\n\n"


def _sse_error(message: str) -> str:
    return f'data: {json.dumps({"event_type": "error", "payload": {"message": message}}, ensure_ascii=False)}\n\n'


async def _sse_events(task_id: str, after_seq: int = 0) -> AsyncIterator[str]:
    """SSE 事件序列：先回放已落库 seq>N（断线/重启重连锚点），再实时监听常驻 bus。

    实时段保持连接（心跳 + 续接后续 run 的事件）；客户端断开由 StreamingResponse 取消生成器，
    finally 注销订阅。
    """
    task = models.get_task(task_id)
    if task is None:
        yield _sse_error("task not found")
        return

    # 先订阅常驻 bus，再回放：回放期间的实时事件落入队列，回放后补发，杜绝缝隙。
    bus = _buses.get(task_id)
    if bus is None:
        bus = hooks.EventBus(task.workspace, task_id, run_id="")
        _buses[task_id] = bus
    q = bus.subscribe()
    replayed = after_seq
    try:
        for ev in models.events_after(task_id, after_seq):
            yield _sse_event(ev)
            replayed = max(replayed, ev.seq)
        # 回放期间广播到队列但未在回放里的事件（seq 去重，避免重复）
        while True:
            try:
                ev = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if ev.seq > replayed:
                yield _sse_event(ev)
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=_SSE_HEARTBEAT_SEC)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield _sse_event(ev)
    finally:
        bus.unsubscribe(q)


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #
def init_task_route() -> APIRouter:
    router = APIRouter()
    models.init_db()

    # ---------------------------------------------------------------- //
    # 任务 CRUD
    # ---------------------------------------------------------------- //
    @router.get("/api/tasks")
    async def list_tasks(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        tasks = models.list_tasks()
        return JSONResponse({"ok": True, "tasks": [_task_public(t) for t in tasks]})

    @router.post("/api/tasks")
    async def create_task(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        title = str(body.get("title") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "error": "title is required."}, status_code=400)
        goal = str(body.get("goal") or "")
        workspace_raw = body.get("workspace")
        # P2 上下文桥：任务锁定发起会话 uid（简报回注聊天历史用）。
        conversation_uid = str(body.get("conversation_uid") or "").strip() or None

        workspace, auto = _normalize_workspace(workspace_raw)
        task = models.create_task(
            title=title,
            workspace=workspace,
            goal=goal,
            conversation_uid=conversation_uid,
        )
        SessionFile(workspace, task.id).ensure()
        logger.info(f"task created: {task.id} title={title!r} workspace={workspace} auto={auto} conv={conversation_uid}")
        return JSONResponse(
            {"ok": True, "task": _task_public(task), "auto_created_workspace": auto}
        )

    @router.get("/api/tasks/{task_id}")
    async def get_task(request: Request, task_id: str):
        if not _is_local_request(request):
            return _forbidden()
        task = models.get_task(task_id)
        if task is None:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        session = SessionFile(task.workspace, task.id)
        return JSONResponse(
            {
                "ok": True,
                "task": _task_public(task),
                "messages": session.read_messages(),
                # Phase 5：events 与 SSE 流同构（忠实 run_id/category/origin），前端刷新持久据此恢复
                "events": [ev.model_dump() for ev in models.events_after(task_id, 0)],
                "runs": [r.model_dump() for r in models.list_runs(task_id)],
            }
        )

    @router.patch("/api/tasks/{task_id}")
    async def patch_task(request: Request, task_id: str):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        allowed = {"title", "goal", "status"}
        fields = {k: v for k, v in body.items() if k in allowed and v is not None}
        task = models.update_task(task_id, fields)
        if task is None:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        return JSONResponse({"ok": True, "task": _task_public(task)})

    @router.delete("/api/tasks/{task_id}")
    async def delete_task(request: Request, task_id: str):
        if not _is_local_request(request):
            return _forbidden()
        ok = models.delete_task(task_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        _discard_task_state(task_id)  # 清理内存注册表，避免泄漏（review HIGH）
        return JSONResponse({"ok": True})

    # ---------------------------------------------------------------- //
    # 工作目录
    # ---------------------------------------------------------------- //
    @router.get("/api/workspaces/root")
    async def workspaces_root(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        root = _default_tasks_root()
        return JSONResponse({"ok": True, "root": str(root)})

    @router.get("/api/workspaces/scan")
    async def workspaces_scan(request: Request, path: str = ""):
        """目录扫描（深度 2）。path 为空 → 默认任务根。返回两级子树。"""
        if not _is_local_request(request):
            return _forbidden()
        base = Path(path).expanduser() if path else _default_tasks_root()
        try:
            base = base.resolve()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid path"}, status_code=400)
        if not base.is_dir():
            return JSONResponse({"ok": False, "error": "path is not a directory"}, status_code=404)

        def _scan_dir(d: Path, depth: int) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            try:
                entries = sorted(d.iterdir(), key=lambda p: p.name.lower())
            except PermissionError:
                return out
            for p in entries:
                if not p.is_dir() or p.name.startswith("."):
                    continue
                node: dict[str, Any] = {"name": p.name, "path": str(p), "children": []}
                if depth > 0:
                    node["children"] = _scan_dir(p, depth - 1)
                out.append(node)
            return out

        tree = _scan_dir(base, depth=1)  # 深度 2 = 根 + 一层子目录
        return JSONResponse({"ok": True, "base": str(base), "tree": tree})

    # ---------------------------------------------------------------- //
    # Phase 2：运行 / SSE / interrupt
    # ---------------------------------------------------------------- //

    @router.post("/api/chat/delegate-task")
    async def chat_delegate_task(request: Request):
        """聊天 agent 委托任务内核执行轻量任务（G7+ 增强，2026-08-09）。

        解决"聊天 AI 不能用工具"：basic_memory_agent 在判断需要查证事实/搜索网络/
        操作文件时，调用此端点把任务转交给任务内核执行（sub-agent 委派范式），
        任务内核有 bash + skill + delegate agents + MCP 全能力，结果作为 tool_message
        注入聊天上下文，AI 用真实结果回复用户，不再编造。

        Body：
          - goal: 明确的执行目标（必填，如"查询 DeepSeek V4 Flash 是否正式发布"）
          - conversation_uid: 当前聊天会话 uid（必填，用于锁定工作目录 + 任务绑定会话）
          - conf_uid: 角色配置 uid（必填）
          - timeout_sec: 最大等待秒（默认 90）
        Response：
          - ok: 是否成功创建并完成
          - summary: 任务最后 AI 文本前 500 字（用于注入 LLM tool_message）
          - status: completed / interrupted / error / timeout / failed
        """
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        goal = str(body.get("goal") or "").strip()
        conv_uid = str(body.get("conversation_uid") or "").strip()
        conf_uid = str(body.get("conf_uid") or "").strip()
        timeout_sec = max(10, min(int(body.get("timeout_sec") or 90), 300))

        if not goal:
            return JSONResponse(
                {"ok": False, "error": "goal is required."}, status_code=400
            )
        if not conv_uid:
            return JSONResponse(
                {"ok": False, "error": "conversation_uid is required."},
                status_code=400,
            )

        # 取会话绑定的工作目录（不取则后端自动建根目录，聊天历史里查得到）
        try:
            from ..chat_history_manager import get_metadata

            meta = get_metadata(conf_uid, conv_uid) if conf_uid else {}
            workspace = str((meta or {}).get("workspace") or "").strip()
        except Exception:
            workspace = ""

        # 2026-08-09 修复：workspace 为空时兜底默认任务根（走与 POST /api/tasks 相同的
        # _normalize_workspace），避免 workspace="" 入库 → Sandbox("") 解析到后端 cwd，
        # 工具在错误目录执行、结果拿不到。
        try:
            workspace, _auto = _normalize_workspace(workspace or None)
        except Exception as e:
            logger.warning(f"[chat-delegate] workspace 兜底失败：{e}")
            return JSONResponse(
                {"ok": False, "error": f"工作目录不可用：{e}"}, status_code=500
            )

        # 创建临时任务（标题 = goal 前 30 字）→ start_run 触发后台执行
        title = goal[:30].replace("\n", " ").strip() or "委托任务"
        try:
            task = models.create_task(
                title=title,
                workspace=workspace,
                goal=goal,
                conversation_uid=conv_uid,
            )
            SessionFile(workspace, task.id).ensure()
            ctx = start_run(task, task_config(), goal, persist_chat_message=False)
        except RunBusyError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        except Exception as e:
            logger.warning(f"[chat-delegate] 创建任务失败：{e}")
            return JSONResponse(
                {"ok": False, "error": f"创建任务失败：{e}"}, status_code=500
            )

        # 轮询直到 run 完成或超时
        run_id = ctx.run.id
        polled = 0
        interval = 1.5
        while polled < timeout_sec:
            await asyncio.sleep(interval)
            polled += interval
            run_row = models.get_run(run_id)
            if run_row is None:
                return JSONResponse(
                    {"ok": False, "status": "failed", "error": "run not found",
                     "task_id": task.id, "run_id": run_id},
                    status_code=500,
                )
            if run_row.status != "running":
                # 取最后 AI 文本作为摘要（tool_message 注入用）
                summary = str(run_row.summary or "").strip()
                # run.summary 可能为空（goal.py 未生成）→ 从事件流抓最长的 assistant 消息
                # （任务常有多条 assistant 消息：完整结果 + 收尾总结，取最长=完整结果优先）
                if not summary:
                    try:
                        events = models.events_after(task.id, 0)
                        best = ""
                        for ev in events:
                            if (
                                ev.event_type == "message"
                                and isinstance(ev.payload, dict)
                                and ev.payload.get("role") == "assistant"
                            ):
                                c = str(ev.payload.get("content") or "").strip()
                                if len(c) > len(best):
                                    best = c
                        summary = best
                    except Exception:
                        pass
                summary = summary[:4000]
                return JSONResponse(
                    {
                        "ok": run_row.status == "completed",
                        "status": run_row.status,
                        "summary": summary or "(无输出)",
                        "task_id": task.id,
                        "run_id": run_id,
                    }
                )

        # 超时：中断 run 并返回当前摘要
        try:
            models.finish_run(run_id, "interrupted", error="delegate-task timeout")
        except Exception:
            pass
        return JSONResponse(
            {"ok": False, "status": "timeout", "summary": "(超时，无结果)",
             "task_id": task.id, "run_id": run_id}
        )

    @router.post("/api/tasks/{task_id}/runs")
    async def start_run_route(request: Request, task_id: str):
        """发起执行 {message?}：后台跑 agent，SSE 事件经 EventBus 广播。"""
        if not _is_local_request(request):
            return _forbidden()
        task = models.get_task(task_id)
        if task is None:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        message = str(body.get("message") or "").strip() or task.goal
        try:
            ctx = start_run(task, task_config(), message)
        except RunBusyError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        return JSONResponse(
            {
                "ok": True,
                "run": ctx.run.model_dump(),
                "busy": False,
            }
        )

    @router.post("/api/tasks/{task_id}/open")
    async def open_task_dir_route(request: Request, task_id: str):
        """在系统文件管理器中打开任务工作目录（或定位单个产物文件）。

        Body（可选）：`{"path": "<绝对路径>"}` → 在文件夹中选中该文件；
        缺省 → 直接打开工作目录。
        - 只允许本机请求（_is_local_request）。
        - 安全边界：path 必须解析后位于任务工作目录内，否则 400（防目录穿越）。
        - 平台差异：Windows `explorer /select`，macOS `open -R`，Linux `xdg-open`。
        - 打开失败 → 500（前端显示提示）；不抛异常到客户端。
        """
        if not _is_local_request(request):
            return _forbidden()
        task = models.get_task(task_id)
        if task is None:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            import os
            import subprocess
            import sys

            ws = Path(task.workspace).resolve()
            raw = str(body.get("path") or "").strip()
            if raw:
                # 2026-08-10：/workspace 前缀反掩码——LLM 工具参数可能用沙箱虚拟
                # 容器路径（sandbox to_container 把宿主路径掩码成 /workspace/...），
                # 历史产物/旧任务点击虚拟路径时映射回真实工作目录，否则必然 400。
                _raw_norm = raw.replace("\\", "/")
                if _raw_norm.startswith("/workspace/"):
                    raw = str(ws / _raw_norm[len("/workspace/"):])
                # 2026-08-10 修复：相对路径基于任务工作目录解析（LLM 以 cwd=workspace
                # 写盘，产物列表里的相对路径如 pomodoro.html 必须映射回 workspace——
                # 否则 .resolve() 按后端进程 cwd 解析，commonpath 比对必然越界报 400）。
                _p = Path(raw).expanduser()
                if not _p.is_absolute():
                    _p = ws / _p
                target = _p.resolve()
                # 目录穿越防护：产物必须落在工作目录内
                try:
                    if os.path.commonpath([str(ws), str(target)]) != str(ws):
                        return JSONResponse(
                            {"ok": False, "error": "文件不在任务工作目录内，请改用「打开工作目录」查看"},
                            status_code=400,
                        )
                except ValueError:
                    return JSONResponse(
                        {"ok": False, "error": "文件不在任务工作目录内，请改用「打开工作目录」查看"},
                        status_code=400,
                    )
                if not target.exists():
                    return JSONResponse(
                        {"ok": False, "error": f"文件不存在：{target}"}, status_code=404
                    )
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", f"/select,{target}"])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target.parent)])
                logger.info(f"task {task_id} reveal file: {target}")
                return JSONResponse({"ok": True, "revealed": str(target)})
            # 缺省：打开工作目录
            if not ws.is_dir():
                return JSONResponse({"ok": False, "error": "工作目录不存在"}, status_code=404)
            if sys.platform == "win32":
                os.startfile(str(ws))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(ws)])
            else:
                subprocess.Popen(["xdg-open", str(ws)])
            logger.info(f"task {task_id} open workspace: {ws}")
            return JSONResponse({"ok": True, "opened": str(ws)})
        except Exception as e:
            logger.exception(f"task {task_id} open failed: {e}")
            return JSONResponse({"ok": False, "error": f"打开失败：{e}"}, status_code=500)

    @router.get("/api/tasks/{task_id}/runs/stream")
    async def runs_stream_route(request: Request, task_id: str, after_seq: int = 0):
        """SSE 事件流：先回放已落库 seq>N，再实时（断线带 after_seq 重连可回放）。"""
        if not _is_local_request(request):
            return _forbidden()
        return StreamingResponse(
            _sse_events(task_id, after_seq),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/api/tasks/{task_id}/interrupt")
    async def interrupt_route(request: Request, task_id: str):
        """打断运行：取消后台 agent 任务 → run_end(status=interrupted)。"""
        if not _is_local_request(request):
            return _forbidden()
        ctx = _runs.get(task_id)
        if ctx is None or not ctx.is_running:
            return JSONResponse({"ok": False, "error": "no running run"}, status_code=404)
        ctx.run_task.cancel()
        return JSONResponse({"ok": True})

    @router.get("/api/tasks/tools")
    async def list_tools_route(request: Request):
        """任务 agent 可用工具列表（sandbox/skill/MCP 及各自状态，plan §6 Phase 2）。

        结果按 TTL 缓存：探测 MCP 会启动子进程，频繁调用即本地 DoS（review MEDIUM）。
        """
        if not _is_local_request(request):
            return _forbidden()
        global _tools_cache
        import time

        now = time.monotonic()
        if _tools_cache is not None and now - _tools_cache[0] < _TOOLS_CACHE_TTL_SEC:
            tools = _tools_cache[1]
        else:
            tools = await graph.available_tools(task_config())
            _tools_cache = (now, tools)
        return JSONResponse({"ok": True, "tools": tools})

    # ---------------------------------------------------------------- //
    # Phase 3：技能库
    # ---------------------------------------------------------------- //
    @router.get("/api/skills")
    async def list_skills_route(request: Request):
        """技能索引（plan §6 Phase 3）：名称/描述/允许工具/所需密钥。"""
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse({"ok": True, "skills": graph.list_skills(task_config())})

    @router.get("/api/agents")
    async def list_agents_route(request: Request):
        """sub-agent 目录（plan §8 6b）：delegate 可用的子 agent 列表。"""
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse({"ok": True, "agents": graph.list_agents(task_config())})

    @router.get("/api/plugins")
    async def list_plugins_route(request: Request):
        """已加载 extensions 插件（plan §8 6c）：plugins/ 目录约定 + .disabled 开关。"""
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse({"ok": True, "plugins": graph.list_plugins(task_config())})

    @router.get("/api/skills/{name}")
    async def get_skill_route(request: Request, name: str):
        """技能详情（含正文 body）：GET /api/skills/{name}。"""
        if not _is_local_request(request):
            return _forbidden()
        detail = graph.skill_detail(name, task_config())
        if detail is None:
            return JSONResponse({"ok": False, "error": "skill not found"}, status_code=404)
        return JSONResponse({"ok": True, "skill": detail})

    # ---------------------------------------------------------------- //
    # Phase 6：上下文压缩
    # ---------------------------------------------------------------- //
    @router.post("/api/tasks/{task_id}/compact")
    async def compact_task_route(request: Request, task_id: str, keep: int = 20):
        """上下文压缩（Phase 6a）：手动触发 checkpoint 消息压缩，长任务可续跑。

        - `keep`: 保留的最近消息条数（默认 20，须 ≥ 1）；消息数 ≤ keep 时 no-op。
        - 运行中的 run → 409：压缩写 checkpoint 与运行中的 checkpoint 写入并发会互相踩。
          409 检查与 checkpoint 写入间存在极短 TOCTOU 窗口（~µs），SQLite WAL 串行化写者，
          最坏是 `database is locked` → 500，不会损坏数据（review MEDIUM 已接受并文档化）。
        - 摘要生成 / checkpoint 写入失败 → 500（压缩不生效）。
        """
        if not _is_local_request(request):
            return _forbidden()
        task = models.get_task(task_id)
        if task is None:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        ctx = _runs.get(task_id)
        if ctx is not None and ctx.is_running:
            return JSONResponse({"ok": False, "error": "task has a running run"}, status_code=409)
        if keep < 1:
            return JSONResponse({"ok": False, "error": "keep must be >= 1"}, status_code=400)
        # 仅用既有 bus（不建、不改 run_id —— review LOW：_bus_for 会覆盖共享 bus 的 run_id）。
        bus = _buses.get(task_id)
        try:
            result = await compaction.compact_task_context(
                task, task_config(), keep_messages=keep, bus=bus
            )
        except Exception as e:
            logger.exception(f"compact task={task_id} failed: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
        return JSONResponse({"ok": True, **result})

    return router
