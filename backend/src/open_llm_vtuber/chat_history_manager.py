import os
import re
import json
import uuid
import threading
from datetime import datetime
from typing import Literal, List, TypedDict, Optional
from loguru import logger


_HISTORY_LOCK = threading.RLock()


def _write_history_file(filepath: str, history_data: list[dict]) -> None:
    """Atomically replace a history file; caller must hold _HISTORY_LOCK."""
    tmp_path = f"{filepath}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, filepath)


class HistoryMessage(TypedDict):
    role: Literal["human", "ai"]
    timestamp: str
    content: str
    # Optional display information for the message
    name: Optional[str]
    avatar: Optional[str]
    # 2026-08-10：消息类别标识（"task_brief"= 任务简报；None = 普通对话）。
    # 前端据此把简报渲染成折叠卡片而非普通气泡，避免技术长文刷屏会话。
    kind: Optional[str]
    # 2026-08-10：简报归属的任务 id —— 同一任务多次 run 只保留最新一条（upsert）。
    task_id: Optional[str]


def _is_internal_task_message(message: dict) -> bool:
    """Return whether a persisted message belongs to the task plumbing.

    Older task-platform versions wrote the delegated goal as a normal human
    message.  That made the implementation detail reappear after a history
    reload.  Keep this compatibility filter at the storage boundary so old
    files are also rendered correctly; new code must not persist these rows.
    """
    kind = message.get("kind")
    if kind in {"task_shell", "task_result", "task_report"}:
        return True
    content = str(message.get("content") or "").strip()
    if message.get("role") == "human" and any(
        marker in content
        for marker in (
            "先尝试执行 date 命令",
            "优先用网络时间 API",
            "获取当前准确时间",
            "获取当前系统时间",
            "获取当前北京时间",
        )
    ):
        return True
    # Older task runs also persisted the task agent's final report as an
    # untyped assistant message.  Hide only the stable task-report prefix;
    # ordinary assistant replies remain untouched.
    if message.get("role") == "ai" and (
        content.startswith("任务完成啦")
        or content.startswith("任务执行完成")
        or content.startswith("任务执行失败")
    ):
        return True
    return False


def _is_safe_filename(filename: str) -> bool:
    """Validate filename for safety and allowed characters"""
    if not filename or len(filename) > 255:
        return False

    # Allow alphanumeric, hyphen, underscore, and common unicode characters
    # Block any filesystem special characters, control characters, and path separators
    pattern = re.compile(r"^[\w\-_\u0020-\u007E\u00A0-\uFFFF]+$")
    return bool(pattern.match(filename))


def _sanitize_path_component(component: str) -> str:
    """Sanitize and validate a path component"""
    # Remove any path components, get just the basename
    sanitized = os.path.basename(component.strip())

    if not _is_safe_filename(sanitized):
        raise ValueError(f"Invalid characters in path component: {component}")

    return sanitized


def _ensure_conf_dir(conf_uid: str) -> str:
    """Ensure the directory for a specific conf exists and return its path"""
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")

    safe_conf_uid = _sanitize_path_component(conf_uid)
    base_dir = os.path.join("chat_history", safe_conf_uid)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _get_safe_history_path(conf_uid: str, history_uid: str) -> str:
    """Get sanitized path for history file"""
    safe_conf_uid = _sanitize_path_component(conf_uid)
    safe_history_uid = _sanitize_path_component(history_uid)
    base_dir = os.path.join("chat_history", safe_conf_uid)
    full_path = os.path.normpath(os.path.join(base_dir, f"{safe_history_uid}.json"))
    if not full_path.startswith(base_dir):
        raise ValueError("Invalid path: Path traversal detected")
    return full_path


def create_new_history(conf_uid: str, workspace: str = "") -> str:
    """Create a new history file with a unique ID and return the history_uid.

    ``workspace``: 会话绑定的工作目录（绝对路径）。重设计 v5 起所有会话必须
    归属一个工作目录；空字符串仅用于向后兼容的内部调用（前端不会再传空）。
    """
    if not conf_uid:
        logger.warning("No conf_uid provided")
        return ""

    # Use uuid.uuid4().hex to generate a UUID without hyphens
    # New format: UUID_YYYY-MM-DD_HH-MM-SS
    history_uid = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex}"
    conf_dir = _ensure_conf_dir(conf_uid)  # conf_uid is sanitized here

    # Create history file with empty metadata
    try:
        filepath = os.path.join(conf_dir, f"{history_uid}.json")
        metadata: dict = {
            "role": "metadata",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if workspace:
            metadata["workspace"] = workspace
        initial_data = [metadata]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to create new history file: {e}")
        return ""

    logger.debug(f"Created new history file with empty metadata: {filepath}")
    return history_uid


def _derive_default_title(content: str) -> str:
    """从用户首条消息生成默认会话标题：压缩空白/换行后取前 10 字。

    仅在该会话尚无标题（用户未自定义）时使用，自定义标题永不覆盖。
    """
    if not content:
        return ""
    compact = re.sub(r"\s+", " ", content).strip()
    return compact[:10]


def _store_message_unlocked(
    conf_uid: str,
    history_uid: str,
    role: Literal["human", "ai"],
    content: str,
    name: str | None = None,
    avatar: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
):
    """Store a message in a specific history file

    Args:
        conf_uid: Configuration unique identifier
        history_uid: History unique identifier
        role: Message role ("human" or "ai")
        content: Message content
        name: Optional display name (default None)
        avatar: Optional avatar URL (default None)
        kind: 2026-08-10 消息类别（"task_brief" 等；None = 普通对话）
        task_id: 2026-08-10 归属任务 id（简报消息用于去重）
    """
    if not conf_uid or not history_uid:
        if not conf_uid:
            logger.warning("Missing conf_uid")
        if not history_uid:
            logger.warning("Missing history_uid")
        return

    filepath = _get_safe_history_path(conf_uid, history_uid)
    logger.debug(f"Storing {role} message to {filepath}")

    history_data = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            logger.error(f"Failed to load history file: {filepath}")
            pass

    now_str = datetime.now().isoformat(timespec="seconds")
    new_item = {
        "role": role,
        "timestamp": now_str,
        "content": content,
    }

    # Add optional display information if provided
    if name is not None:
        new_item["name"] = name
    if avatar is not None:
        new_item["avatar"] = avatar
    if kind is not None:
        new_item["kind"] = kind
    if task_id is not None:
        new_item["task_id"] = task_id

    history_data.append(new_item)

    # 默认标题：首条用户消息自动取名（前 10 字），已有标题则保留（自定义优先）。
    if role == "human":
        meta = (
            history_data[0]
            if history_data and history_data[0].get("role") == "metadata"
            else None
        )
        if meta is None:
            meta = {"role": "metadata", "timestamp": now_str}
            history_data.insert(0, meta)
        if not meta.get("title"):
            meta["title"] = _derive_default_title(content)

    _write_history_file(filepath, history_data)
    logger.debug(f"Successfully stored {role} message")


def store_message(*args, **kwargs):
    """Append one message without allowing concurrent read-modify-write loss."""
    with _HISTORY_LOCK:
        return _store_message_unlocked(*args, **kwargs)


def _upsert_task_brief_unlocked(
    conf_uid: str,
    history_uid: str,
    task_id: str,
    content: str,
    name: str | None = None,
) -> str | None:
    """2026-08-10：任务简报「去重 upsert」——同一任务多次 run 只保留最新一条。

    - 在会话历史里查找 kind=="task_brief" 且 task_id 匹配的消息：
      * 找到 → 原地替换 content/name/timestamp（保持原消息位置，历史顺序稳定）
      * 没找到 → 追加新消息（kind="task_brief" + task_id）
    - 解决「同一任务 3 次 run 往会话塞 3 条长简报刷屏」问题。
    - 返回 "updated" / "inserted"；失败（conf/uid 缺失、文件损坏）返回 None，不抛异常。
    """
    if not conf_uid or not history_uid or not task_id:
        return None


    try:
        filepath = _get_safe_history_path(conf_uid, history_uid)
        history_data: list[dict] = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
            except Exception:
                logger.error(f"Failed to load history file: {filepath}")
                return None

        now_str = datetime.now().isoformat(timespec="seconds")
        # 找同任务简报：kind == task_brief 且 task_id 匹配（兼容旧字段缺失）
        target_idx = -1
        for i, item in enumerate(history_data):
            if item.get("kind") == "task_brief" and item.get("task_id") == task_id:
                target_idx = i
                break
        if target_idx >= 0:
            # 原地替换：保留 role/timestamp 顺序位置，仅更新内容
            history_data[target_idx]["content"] = content
            history_data[target_idx]["timestamp"] = now_str
            if name is not None:
                history_data[target_idx]["name"] = name
            action = "updated"
        else:
            new_item: dict = {
                "role": "ai",
                "timestamp": now_str,
                "content": content,
                "kind": "task_brief",
                "task_id": task_id,
            }
            if name is not None:
                new_item["name"] = name
            history_data.append(new_item)
            action = "inserted"

        _write_history_file(filepath, history_data)
        logger.debug(f"[brief] {action} task_brief({task_id}) -> {filepath}")
        return action
    except Exception as e:  # fail-soft：简报去重失败静默，不阻断 run
        logger.debug(f"[brief] upsert_task_brief 失败（静默）：{e}")
        return None


def upsert_task_brief(*args, **kwargs) -> str | None:
    """Upsert a task brief under the same history write lock as chat messages."""
    with _HISTORY_LOCK:
        return _upsert_task_brief_unlocked(*args, **kwargs)


def get_metadata(conf_uid: str, history_uid: str) -> dict:
    """Get metadata from history file"""
    if not conf_uid or not history_uid:
        return {}

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        return {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            history_data = json.load(f)

        if history_data and history_data[0]["role"] == "metadata":
            return history_data[0]
    except Exception as e:
        logger.error(f"Failed to get metadata: {e}")
    return {}


def _update_metadate_unlocked(conf_uid: str, history_uid: str, metadata: dict) -> bool:
    """Set metadata in history file

    Updates existing metadata with new fields, preserving existing ones.
    If no metadata exists, creates new metadata entry.
    """
    if not conf_uid or not history_uid:
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            history_data = json.load(f)

        if history_data and history_data[0]["role"] == "metadata":
            # Update existing metadata while preserving other fields
            history_data[0].update(metadata)
        else:
            # Create new metadata with timestamp if none exists
            new_metadata = {
                "role": "metadata",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            new_metadata.update(metadata)  # Add new fields
            history_data.insert(0, new_metadata)

        _write_history_file(filepath, history_data)

        logger.debug(f"Updated metadata for history {history_uid}")
        return True
    except Exception as e:
        logger.error(f"Failed to set metadata: {e}")
    return False


def update_metadate(conf_uid: str, history_uid: str, metadata: dict) -> bool:
    """Update metadata under the same lock as message append/upsert."""
    with _HISTORY_LOCK:
        return _update_metadate_unlocked(conf_uid, history_uid, metadata)


def get_history(conf_uid: str, history_uid: str) -> List[HistoryMessage]:
    """Read chat history for the given conf_uid and history_uid"""
    if not conf_uid or not history_uid:
        if not conf_uid:
            logger.warning("Missing conf_uid")
        if not history_uid:
            logger.warning("Missing history_uid")
        return []

    filepath = _get_safe_history_path(conf_uid, history_uid)

    if not os.path.exists(filepath):
        logger.warning(f"History file not found: {filepath}")
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            history_data = json.load(f)
            # Filter out metadata
            visible: list[dict] = []
            previous_was_task_shell = False
            for msg in history_data:
                if msg["role"] == "metadata":
                    continue
                legacy_internal = (
                    msg.get("role") == "human" and previous_was_task_shell
                )
                if not _is_internal_task_message(msg) and not legacy_internal:
                    visible.append(msg)
                previous_was_task_shell = msg.get("kind") == "task_shell"
            return visible
    except Exception:
        return []


def delete_history(conf_uid: str, history_uid: str) -> bool:
    """Delete a specific history file"""
    if not conf_uid or not history_uid:
        logger.warning("Missing conf_uid or history_uid")
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.debug(f"Successfully deleted history file: {filepath}")
            return True
    except Exception as e:
        logger.error(f"Failed to delete history file: {e}")
    return False


def clear_all_histories(conf_uid: str) -> int:
    """删除该角色下全部会话记录（仅对话转写 json，不含 affection/quotes 等）。

    用于「存量无目录会话一次性清理」。返回删除的文件数。
    """
    if not conf_uid:
        return 0
    conf_dir = _ensure_conf_dir(conf_uid)
    removed = 0
    try:
        for filename in os.listdir(conf_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(conf_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 只删对话转写（JSON 数组首条 role=metadata）；其他数据文件跳过
                if isinstance(data, list) and data and data[0].get("role") == "metadata":
                    os.remove(filepath)
                    removed += 1
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Failed to clear histories: {e}")
    if removed:
        logger.info(f"Cleared {removed} history files for conf {conf_uid}")
    return removed


def get_history_list(conf_uid: str, keep_uid: str | None = None) -> List[dict]:
    """Get list of histories with their latest messages.

    ``keep_uid``: a history_uid that must NOT be auto-cleaned even if empty — used
    to protect the just-created (still-empty) session so "新建对话" isn't immediately
    deleted by the empty-history sweep. Also skips non-list JSON files (e.g.
    affection.json / quotes.json) that aren't conversation transcripts.
    """
    if not conf_uid:
        return []

    histories = []
    conf_dir = _ensure_conf_dir(conf_uid)
    empty_history_uids = []

    try:
        for filename in os.listdir(conf_dir):
            if not filename.endswith(".json"):
                continue

            history_uid = filename[:-5]
            filepath = os.path.join(conf_dir, filename)

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    messages = json.load(f)

                # Only conversation transcripts are JSON arrays of message dicts.
                # affection.json / quotes.json are plain dicts -> skip silently.
                if not isinstance(messages, list):
                    continue

                # Filter out metadata for checking if history is empty
                actual_messages = [
                    msg
                    for msg in messages
                    if msg["role"] != "metadata" and not _is_internal_task_message(msg)
                ]
                if not actual_messages:
                    empty_history_uids.append(history_uid)
                    # 刚创建的空会话（keep_uid，前端「在此目录新建会话」后仍在等待）
                    # 也要进列表：前端靠 history-list 里的 workspace 解析 currentWorkspace，
                    # 空会话不进列表会导致「选择工作目录」引导常显/闪烁。
                    if history_uid != keep_uid:
                        continue
                    latest_message = None
                else:
                    latest_message = actual_messages[-1]
                # metadata（首条）里可含用户自定义/自动生成的会话标题
                metadata = (
                    messages[0]
                    if messages and messages[0].get("role") == "metadata"
                    else {}
                )
                history_info = {
                    "uid": history_uid,
                    "title": metadata.get("title"),
                    "workspace": metadata.get("workspace") or "",
                    "latest_message": latest_message,
                    "timestamp": (
                        latest_message["timestamp"]
                        if latest_message
                        else metadata.get("timestamp")  # 空会话用创建时间，前端倒序排在顶部
                    ),
                }
                histories.append(history_info)
            except Exception as e:
                logger.error(f"Error reading history file {filename}: {e}")
                continue

        # Clean up empty histories if there are other non-empty ones (but never the
        # session the client is actively in, which starts empty).
        if len(empty_history_uids) > 0 and len(os.listdir(conf_dir)) > 1:
            for uid in empty_history_uids:
                if uid == keep_uid:
                    continue
                try:
                    os.remove(os.path.join(conf_dir, f"{uid}.json"))
                    logger.info(f"Removed empty history file: {uid}")
                except Exception as e:
                    logger.error(f"Failed to remove empty history file {uid}: {e}")

        histories.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else "", reverse=True
        )
        return histories

    except Exception as e:
        logger.error(f"Error listing histories: {e}")
        return []


def modify_latest_message(
    conf_uid: str,
    history_uid: str,
    role: Literal["human", "ai", "system"],
    new_content: str,
) -> bool:
    """Modify the latest message in a specific history file if it matches the given role"""
    if not conf_uid or not history_uid:
        logger.warning("Missing conf_uid or history_uid")
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        logger.warning(f"History file not found: {filepath}")
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            history_data = json.load(f)

        if not history_data:
            logger.warning("History is empty")
            return False

        latest_message = history_data[-1]
        if latest_message["role"] != role:
            logger.warning(
                f"Latest message role ({latest_message['role']}) doesn't match requested role ({role})"
            )
            return False

        latest_message["content"] = new_content
        _write_history_file(filepath, history_data)

        logger.debug(f"Successfully modified latest {role} message")
        return True

    except Exception as e:
        logger.error(f"Failed to modify latest message: {e}")
        return False


def rename_history_file(
    conf_uid: str, old_history_uid: str, new_history_uid: str
) -> bool:
    """Rename a history file with a new history_uid"""
    if not conf_uid or not old_history_uid or not new_history_uid:
        logger.warning("Missing required parameters for rename")
        return False

    old_filepath = _get_safe_history_path(conf_uid, old_history_uid)
    new_filepath = _get_safe_history_path(conf_uid, new_history_uid)

    try:
        if os.path.exists(old_filepath):
            os.rename(old_filepath, new_filepath)
            logger.info(
                f"Renamed history file from {old_history_uid} to {new_history_uid}"
            )
            return True
    except Exception as e:
        logger.error(f"Failed to rename history file: {e}")
    return False
