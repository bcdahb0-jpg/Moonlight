"""JSONL 会话树（plan §4.2，参考 pi session-manager）。

- 位置：`<workspace>/.pi/tasks/<task-id>/session.jsonl`
- 每行独立 JSON，坏行跳过（容错）；append-only（审计 / UI 投影真相源）。
- header 存 cwd = 任务 ↔ 文件夹绑定。
- seq 分配：按 task 单调递增，供 SSE 断线重连锚点（plan §4.3）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from loguru import logger

from .models import now_iso

SESSION_VERSION = 1


def session_dir(workspace: str, task_id: str) -> Path:
    """会话目录：<workspace>/.pi/tasks/<task-id>/"""
    return Path(workspace) / ".pi" / "tasks" / task_id


def session_path(workspace: str, task_id: str) -> Path:
    return session_dir(workspace, task_id) / "session.jsonl"


class SessionFile:
    """一个任务对应的 JSONL 会话文件。

    职责：
    - 追加写（消息 / 事件 / compaction / header）
    - 顺序读取（坏行跳过）
    - seq 计数器（按 task 单调递增，跨进程由 SQLite task_events 兜底）
    """

    def __init__(self, workspace: str, task_id: str):
        self.workspace = str(Path(workspace).resolve())
        self.task_id = task_id
        self.path = session_path(self.workspace, task_id)
        self._seq = 0
        self._seq_loaded = False

    # ------------------------------------------------------------------ //
    # 初始化 / 结构
    # ------------------------------------------------------------------ //
    def ensure(self) -> Path:
        """建目录 + 写 header（幂等）。返回 session 文件路径。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._append(
                {
                    "type": "session",
                    "version": SESSION_VERSION,
                    "id": self.task_id,
                    "timestamp": now_iso(),
                    "cwd": self.workspace,
                }
            )
        return self.path

    def _append(self, entry: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ //
    # 追加写入
    # ------------------------------------------------------------------ //
    def add_message(
        self,
        message: dict[str, Any],
        parent_id: Optional[str] = None,
        entry_id: Optional[str] = None,
    ) -> str:
        """追加一条消息条目。返回 entry id。"""
        eid = entry_id or uuid.uuid4().hex
        self._append(
            {
                "type": "message",
                "id": eid,
                "parentId": parent_id,
                "timestamp": now_iso(),
                "message": message,
            }
        )
        return eid

    def add_event(self, event: Any) -> None:
        """追加一条 event 条目（不可从消息重建的状态事件，plan §4.2/§4.3）。"""
        self._append(
            {
                "type": "event",
                "id": uuid.uuid4().hex,
                "seq": event.seq,
                "task_id": self.task_id,
                "event": {
                    "event_type": event.event_type,
                    "run_id": event.run_id,
                    "category": event.category,
                    "origin": event.origin,
                    "payload": event.payload,
                    "created_at": event.created_at,
                },
            }
        )

    def add_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        parent_id: Optional[str] = None,
    ) -> str:
        """追加 compaction 条目（旧消息摘要化，plan §4.2 / §5.4）。

        `first_kept_entry_id` 锚定保留区首条消息的 LangGraph 消息 id（checkpoint id 空间，
        非本 JSONL 的 entry id —— 两套 id 空间不相通）。
        """
        cid = uuid.uuid4().hex
        self._append(
            {
                "type": "compaction",
                "id": cid,
                "parentId": parent_id,
                "timestamp": now_iso(),
                "summary": summary,
                "firstKeptEntryId": first_kept_entry_id,
            }
        )
        return cid

    def add_compaction_failed(self, entry_id: str, error: str) -> None:
        """追加 compaction 失败条目（审计真相源：JSONL 里失败的压缩显式可辨）。"""
        self._append(
            {
                "type": "compaction_failed",
                "id": uuid.uuid4().hex,
                "parentId": entry_id,
                "timestamp": now_iso(),
                "error": error,
            }
        )

    # ------------------------------------------------------------------ //
    # seq 计数（SSE 锚点）
    # ------------------------------------------------------------------ //
    def next_seq(self) -> int:
        """按 task 单调递增的 seq。

        跨会话续接以 SQLite task_events.last_seq（权威）为基准，JSONL event
        为兜底（plan §4.3 双落）。惰性加载一次。
        """
        if not self._seq_loaded:
            self._seq = max(self._max_seq_sqlite(), self._max_seq_jsonl())
            self._seq_loaded = True
        self._seq += 1
        return self._seq

    def _max_seq_sqlite(self) -> int:
        from . import models

        return models.last_seq(self.task_id)

    def _max_seq_jsonl(self) -> int:
        mx = 0
        for entry in self.iter_entries():
            if isinstance(entry, dict) and entry.get("type") == "event":
                try:
                    s = int(entry.get("seq", 0))
                    mx = max(mx, s)
                except (TypeError, ValueError):
                    continue
        return mx

    # ------------------------------------------------------------------ //
    # 读取
    # ------------------------------------------------------------------ //
    def iter_entries(self) -> Iterator[dict[str, Any]]:
        """顺序读全部条目；坏行跳过（容错，plan §9-8）。"""
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"session: skipping bad JSONL line in {self.path}")
                    continue

    def read_messages(self) -> list[dict[str, Any]]:
        """投影为消息列表（供 API 详情 + 前端历史回放）。"""
        out: list[dict[str, Any]] = []
        for e in self.iter_entries():
            if e.get("type") == "message":
                out.append(
                    {
                        "id": e.get("id"),
                        "parentId": e.get("parentId"),
                        "timestamp": e.get("timestamp"),
                        "message": e.get("message"),
                    }
                )
        return out

    def read_events(self) -> list[dict[str, Any]]:
        """投影为事件列表（含 seq）。"""
        out: list[dict[str, Any]] = []
        for e in self.iter_entries():
            if e.get("type") == "event":
                out.append(e)
        return out
