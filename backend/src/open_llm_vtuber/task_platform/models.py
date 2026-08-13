"""任务平台实体 / 事件模型 + SQLite 存储层（plan §4.1 / §4.3）。

- pydantic v2 模型：Task / Run / TaskEvent / SkillSpec
- SQLite 用 stdlib `sqlite3`：WAL + check_same_thread=False + 每请求新连接（plan §4.1 v2.5 并发决策）。
- 不依赖现有 conversations / memory / mcp 模块（单向依赖铁律）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# pydantic 模型
# --------------------------------------------------------------------------- #


class Task(BaseModel):
    """任务元数据（对应 tasks 表）。"""

    id: str
    title: str
    workspace: str  # realpath 绝对路径（任务根）
    goal: str = ""
    status: str = "active"  # active|paused|completed|archived
    created_at: str = ""
    updated_at: str = ""
    last_run_id: Optional[str] = None
    # P2 上下文桥：任务锁定的发起会话 uid（聊天历史注入简报用；旧任务为空）。
    conversation_uid: Optional[str] = None


class Run(BaseModel):
    """一次任务执行（对应 runs 表）。"""

    id: str
    task_id: str
    status: str = "running"  # running|completed|interrupted|error
    started_at: str = ""
    ended_at: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[str] = None


class TaskEvent(BaseModel):
    """SSE 事件（对应 task_events 表 + JSONL event 条目双落，plan §4.3）。"""

    seq: int
    event_type: str  # run_start|message|tool_call|tool_result|status|clarify_requested|progress|run_end|run_error|error
    task_id: str
    run_id: str
    category: str = "message"  # message|trace|outputs|error
    origin: str = "core"  # core（任务内核）| shell（人设外壳）——G7 分流
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class SkillSpec(BaseModel):
    """SKILL.md frontmatter 契约（plan §5.1）。"""

    name: str
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    required_secrets: list[str] = Field(default_factory=list)
    path: str = ""


# --------------------------------------------------------------------------- #
# 时间工具
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    """UTC ISO-8601 时间戳（毫秒精度，保证同一秒内的先后排序）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


# --------------------------------------------------------------------------- #
# SQLite 存储层
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    goal        TEXT DEFAULT '',
    status      TEXT DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_run_id TEXT,
    conversation_uid TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status     TEXT DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    error      TEXT,
    summary    TEXT
);
CREATE TABLE IF NOT EXISTS task_events (
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    run_id     TEXT NOT NULL DEFAULT '',
    category   TEXT NOT NULL DEFAULT 'message',
    origin     TEXT NOT NULL DEFAULT 'core',
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, seq);
"""

_thread_local = threading.local()


def db_path() -> Path:
    """任务元数据 SQLite 路径（conf 可配，默认 backend/task_platform.db）。"""
    from .conf_bridge import task_config

    p = Path(task_config().db_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def get_conn() -> sqlite3.Connection:
    """每请求/每协程新建 SQLite 连接（WAL + check_same_thread=False）。

    plan §4.1 v2.5：FastAPI 多请求并发 → 不用长连接共享，写操作由单线程串行化。
    """
    conn = sqlite3.connect(str(db_path()), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


_MIGRATABLE_TABLES = frozenset({"task_events", "tasks"})


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等迁移：旧库缺列时 ALTER ADD COLUMN（Phase 5：task_events 需按 run_id 分组）。

    表名硬性白名单（防未来误把外部输入拼进 DDL）。
    """
    if table not in _MIGRATABLE_TABLES:
        raise ValueError(f"unsupported table for migration: {table!r}")
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    """建表（幂等）。启动时 + 首次访问时调用。"""
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        # 旧库迁移：task_events 补 run_id / category / origin（SSE 回放需忠实字段）
        _ensure_column(conn, "task_events", "run_id", "run_id TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "task_events", "category", "category TEXT NOT NULL DEFAULT 'message'")
        _ensure_column(conn, "task_events", "origin", "origin TEXT NOT NULL DEFAULT 'core'")
        # P2：tasks 补 conversation_uid（任务锁定发起会话）
        _ensure_column(conn, "tasks", "conversation_uid", "conversation_uid TEXT")
        conn.execute(
            """
            UPDATE tasks
            SET status='completed', updated_at=(
                SELECT COALESCE(runs.ended_at, tasks.updated_at)
                FROM runs
                WHERE runs.id=tasks.last_run_id
            )
            WHERE status='active'
              AND last_run_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM runs
                  WHERE runs.id=tasks.last_run_id AND runs.status='completed'
              )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> None:
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
        tuple(data.values()),
    )


# --------------------------------------------------------------------------- #
# Tasks CRUD
# --------------------------------------------------------------------------- #


def create_task(
    title: str,
    workspace: str,
    goal: str = "",
    task_id: Optional[str] = None,
    conversation_uid: Optional[str] = None,
) -> Task:
    import uuid

    # UX 契约（M1）：标题兜底生成，避免「未命名任务」堆满列表。
    if not title or not title.strip():
        title = f"我的任务 · {datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC"

    conn = get_conn()
    try:
        tid = task_id or uuid.uuid4().hex
        ts = now_iso()
        _insert(
            conn,
            "tasks",
            {
                "id": tid,
                "title": title,
                "workspace": workspace,
                "goal": goal,
                "status": "active",
                "created_at": ts,
                "updated_at": ts,
                "last_run_id": None,
                "conversation_uid": conversation_uid,
            },
        )
        conn.commit()
        return Task(
            id=tid,
            title=title,
            workspace=workspace,
            goal=goal,
            status="active",
            created_at=ts,
            updated_at=ts,
            conversation_uid=conversation_uid,
        )
    finally:
        conn.close()


def get_task(task_id: str) -> Optional[Task]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None
    finally:
        conn.close()


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        workspace=row["workspace"],
        goal=row["goal"] or "",
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_run_id=row["last_run_id"],
        conversation_uid=row["conversation_uid"] if "conversation_uid" in row.keys() else None,
    )


def list_tasks() -> list[Task]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
        return [_task_from_row(r) for r in rows]
    finally:
        conn.close()


def update_task(task_id: str, fields: dict[str, Any]) -> Optional[Task]:
    """PATCH：只改给定字段 + 刷新 updated_at。返回更新后的 Task 或 None。"""
    if not fields:
        return get_task(task_id)
    conn = get_conn()
    try:
        sets = ", ".join(f"{k}=?" for k in fields)
        params = tuple(fields.values()) + (task_id,)
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", params)
        conn.execute(
            "UPDATE tasks SET updated_at=? WHERE id=?",
            (now_iso(), task_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None
    finally:
        conn.close()


def delete_task(task_id: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Runs CRUD
# --------------------------------------------------------------------------- #


def create_run(task_id: str, run_id: Optional[str] = None) -> Run:
    import uuid

    conn = get_conn()
    try:
        rid = run_id or uuid.uuid4().hex
        ts = now_iso()
        _insert(
            conn,
            "runs",
            {
                "id": rid,
                "task_id": task_id,
                "status": "running",
                "started_at": ts,
                "ended_at": None,
                "error": None,
                "summary": None,
            },
        )
        conn.execute(
            "UPDATE tasks SET last_run_id=?, status='active', updated_at=? WHERE id=?",
            (rid, now_iso(), task_id),
        )
        conn.commit()
        return Run(id=rid, task_id=task_id, status="running", started_at=ts)
    finally:
        conn.close()


def finish_run(
    run_id: str,
    status: str,
    error: Optional[str] = None,
    summary: Optional[str] = None,
) -> None:
    conn = get_conn()
    try:
        task_row = conn.execute(
            "SELECT task_id FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        conn.execute(
            "UPDATE runs SET status=?, ended_at=?, error=?, summary=? WHERE id=?",
            (status, now_iso(), error, summary, run_id),
        )
        if task_row is not None and status == "completed":
            conn.execute(
                """
                UPDATE tasks
                SET status='completed', updated_at=?
                WHERE id=? AND last_run_id=?
                  AND NOT EXISTS (
                      SELECT 1 FROM runs
                      WHERE task_id=? AND status='running'
                  )
                """,
                (
                    now_iso(),
                    task_row["task_id"],
                    run_id,
                    task_row["task_id"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_run(run_id: str) -> Optional[Run]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _run_from_row(row) if row else None
    finally:
        conn.close()


def _run_from_row(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        task_id=row["task_id"],
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        error=row["error"],
        summary=row["summary"],
    )


def list_runs(task_id: str) -> list[Run]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM runs WHERE task_id=? ORDER BY started_at DESC", (task_id,)
        ).fetchall()
        return [_run_from_row(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# task_events（SSE 锚点持久化，plan §4.3）
# --------------------------------------------------------------------------- #


def append_event(event: TaskEvent) -> TaskEvent:
    """落库一条事件（seq 由调用方按 task 单调递增分配，见 session.next_seq）。"""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO task_events (task_id, seq, event_type, run_id, category, origin, payload, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                event.task_id,
                event.seq,
                event.event_type,
                event.run_id,
                event.category,
                event.origin,
                json.dumps(event.payload, ensure_ascii=False),
                event.created_at,
            ),
        )
        conn.commit()
        return event
    finally:
        conn.close()


def events_after(task_id: str, seq: int) -> list[TaskEvent]:
    """断线重连锚点：取 seq>N 的已落库事件（按序回放）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? AND seq>? ORDER BY seq",
            (task_id, seq),
        ).fetchall()
        return [_event_from_row(r) for r in rows]
    finally:
        conn.close()


def _event_from_row(row: sqlite3.Row) -> TaskEvent:
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return TaskEvent(
        seq=row["seq"],
        event_type=row["event_type"],
        task_id=row["task_id"],
        # 旧行（迁移前）run_id 为空 → 回退 task_id；新行持久化真实 run_id。
        # 注意：迁移前写入的 tool_call/tool_result 行 category/origin 也会回退为 message/core
        # （列默认值），仅影响 Phase 4 及更早的测试库，新写入始终忠实。
        run_id=row["run_id"] or row["task_id"],
        category=row["category"],
        origin=row["origin"],
        payload=payload,
        created_at=row["created_at"],
    )


def last_seq(task_id: str) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(seq) AS m FROM task_events WHERE task_id=?", (task_id,)
        ).fetchone()
        return row["m"] if row and row["m"] is not None else 0
    finally:
        conn.close()
