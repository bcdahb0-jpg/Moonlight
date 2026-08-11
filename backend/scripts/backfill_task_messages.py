"""一次性回填脚本：把任务平台的对话消息回填进发起会话的 chat_history。

背景（2026-08-10）：任务平台 v2 的消息（用户指令 / AI 回复）此前只写入
task session（<workspace>/.pi/tasks/<task-id>/session.jsonl），**从不进
chat_history**——重启后加载历史会话只剩任务简报，对话全丢（用户反馈
"历史对话没有了"）。本脚本把存量任务的 session 消息回填到对应会话：

- 幂等：chat_history 已存在相同 role+content 的消息则跳过，可重复执行；
- 时间线保真：回填消息使用 session.jsonl 的**原始 timestamp**；对已回填过
  但时间戳被覆盖的消息（旧版 store_message 写入时打的是回填时刻时间戳），
  按 role+content 匹配后修正回原始时间戳，再统一按 epoch 排序重写
  （session 用 UTC Z 后缀、简报用本地时间，_ts_epoch 统一解释），
  保证 user → ai → 简报 的正确时间线；
- 只处理 conversation_uid 非空的任务；fail-soft，单任务失败不中断。

用法：cd backend && ./.venv/Scripts/python.exe scripts/backfill_task_messages.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.open_llm_vtuber.chat_history_manager import (  # noqa: E402
    _get_safe_history_path,
)
from src.open_llm_vtuber.task_platform import models  # noqa: E402
from src.open_llm_vtuber.task_platform.session import SessionFile  # noqa: E402


def _ts_epoch(t: str) -> float:
    """时间戳 → epoch 秒（统一时区：Z=UTC、naive=本地时区；失败排最后）。"""
    if not t:
        return float("inf")
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()  # naive → 按本机本地时区解释
        return dt.timestamp()
    except Exception:
        return float("inf")


def _load_items(filepath: Path) -> list[dict]:
    if not filepath.exists():
        return []
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_sorted(filepath: Path, meta: dict, rest: list[dict]) -> None:
    """metadata 固定 index 0，其余按时间戳排序后写盘。"""
    rest.sort(key=lambda m: _ts_epoch(str(m.get("timestamp") or "")))
    if not meta.get("title"):
        first_human = next((m for m in rest if m.get("role") == "human"), None)
        if first_human:
            content = str(first_human.get("content") or "").strip()
            meta["title"] = content[:10] if content else "未命名会话"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        json.dumps([meta] + rest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def backfill_task(task: models.Task, conf_uid: str) -> int:
    """回填单个任务：返回新增条数（跳过已存在的；顺带修正时间戳 + 排序）。"""
    conv_uid = (task.conversation_uid or "").strip()
    if not conv_uid:
        return 0
    sf = SessionFile(task.workspace, task.id)
    msgs = sf.read_messages()
    if not msgs:
        return 0

    filepath = Path(_get_safe_history_path(conf_uid, conv_uid))
    items = _load_items(filepath)

    # session 消息 → (role, content) → 原始 timestamp 映射
    ts_map: dict[tuple[str, str], str] = {}
    for entry in msgs:
        msg = entry.get("message") or {}
        role = str(msg.get("role") or "").strip()
        content = str(msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        ch_role = "human" if role == "user" else "ai"
        ts_map[(ch_role, content)] = str(entry.get("timestamp") or "")

    # 1) 修正既有消息时间戳（旧版回填打的是回填时刻，覆盖回 session 原始值）
    existing = set()
    for m in items:
        if m.get("role") == "metadata":
            continue
        key = (str(m.get("role") or ""), str(m.get("content") or ""))
        existing.add(key)
        orig = ts_map.get(key)
        if orig and str(m.get("timestamp") or "") != orig:
            m["timestamp"] = orig

    # 2) 构造待回填消息（用 session 原始时间戳）
    new_msgs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in msgs:
        msg = entry.get("message") or {}
        role = str(msg.get("role") or "").strip()
        content = str(msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        ch_role = "human" if role == "user" else "ai"
        key = (ch_role, content)
        if key in existing or key in seen:
            continue
        seen.add(key)
        new_msgs.append(
            {
                "role": ch_role,
                "timestamp": str(entry.get("timestamp") or ""),
                "content": content,
            }
        )

    meta = next((m for m in items if m.get("role") == "metadata"), None)
    if meta is None:
        meta = {
            "role": "metadata",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    rest = [m for m in items if m.get("role") != "metadata"] + new_msgs
    _write_sorted(filepath, meta, rest)
    return len(new_msgs)


def main() -> int:
    import yaml  # noqa: E402

    from src.open_llm_vtuber.task_platform.conf_bridge import CONF_PATH  # noqa: E402

    conf_path = Path(CONF_PATH)
    if not conf_path.exists():
        print(f"conf.yaml 不存在: {conf_path}")
        return 1
    cc = (yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}).get(
        "character_config"
    ) or {}
    conf_uid = str(cc.get("conf_uid") or cc.get("conf_name") or "").strip()
    if not conf_uid:
        print("conf.yaml 缺少 conf_uid/conf_name")
        return 1

    tasks = models.list_tasks()
    total_written = 0
    for task in tasks:
        try:
            n = backfill_task(task, conf_uid)
            if n:
                print(f"task={task.id} conv={task.conversation_uid} 回填 {n} 条")
            total_written += n
        except Exception as e:
            print(f"task={task.id} 回填失败（跳过）: {e}")
    print(f"\n完成：{len(tasks)} 个任务，共回填 {total_written} 条消息（幂等）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
