"""针对本次项目审计修复的回归测试。"""

import asyncio
from pathlib import Path

from src.open_llm_vtuber.contracts import build_server_message
from src.open_llm_vtuber.layers.domain.readiness import all_passed
from src.open_llm_vtuber.task_platform import mcp_client, models
from src.open_llm_vtuber.task_platform.conf_bridge import (
    McpServerConfig,
    TaskPlatformConfig,
)


def test_intent_event_is_registered():
    message = build_server_message(
        {
            "type": "intent-event",
            "intent": "chat",
            "emotion": "happy",
            "source": "rule",
        }
    )
    assert message.model_dump()["type"] == "intent-event"


def test_optional_readiness_check_does_not_block():
    assert all_passed(
        [
            {"id": "llm", "passed": True, "required": True},
            {"id": "ollama", "passed": False, "required": False},
        ]
    )


def test_task_route_static_tools_path_precedes_dynamic_path():
    from src.open_llm_vtuber.task_platform.task_route import init_task_route

    paths = [route.path for route in init_task_route().routes]
    assert paths.index("/api/tasks/tools") < paths.index("/api/tasks/{task_id}")


def test_run_lifecycle_updates_task_status(tmp_path, monkeypatch):
    import src.open_llm_vtuber.task_platform.conf_bridge as conf_bridge

    monkeypatch.setattr(
        conf_bridge,
        "_cached",
        TaskPlatformConfig(db_path=str(Path(tmp_path) / "tasks.db")),
    )
    models.init_db()
    task = models.create_task("repair", str(tmp_path))
    first = models.create_run(task.id)
    models.finish_run(first.id, "completed")
    assert models.get_task(task.id).status == "completed"

    second = models.create_run(task.id)
    assert models.get_task(task.id).status == "active"
    models.finish_run(second.id, "interrupted")
    assert models.get_task(task.id).status == "active"


def test_init_db_repairs_completed_task_status(tmp_path, monkeypatch):
    import sqlite3
    import src.open_llm_vtuber.task_platform.conf_bridge as conf_bridge

    db_path = Path(tmp_path) / "legacy.db"
    monkeypatch.setattr(
        conf_bridge,
        "_cached",
        TaskPlatformConfig(db_path=str(db_path)),
    )
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, workspace TEXT NOT NULL,
            goal TEXT DEFAULT '', status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_run_id TEXT
        );
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT DEFAULT 'running',
            started_at TEXT NOT NULL, ended_at TEXT, error TEXT, summary TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("task", "legacy", str(tmp_path), "", "active", "2026-01-01", "2026-01-01", "run"),
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run", "task", "completed", "2026-01-01", "2026-01-02", None, None),
    )
    conn.commit()
    conn.close()

    models.init_db()
    assert models.get_task("task").status == "completed"


def test_mcp_exception_group_exposes_leaf_error(tmp_path):
    config = TaskPlatformConfig(
        db_path=str(Path(tmp_path) / "tasks.db"),
        mcp_servers=[
            McpServerConfig(
                name="broken",
                transport="stdio",
                command="uvx",
                args=["mcp-server-broken"],
            )
        ],
    )

    class BrokenClient:
        async def get_tools(self, server_name=None):
            raise ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [FileNotFoundError("uvx not found")],
            )

    async def run():
        return await mcp_client.probe_servers(
            config, client_factory=lambda _cfg: BrokenClient()
        )

    result = asyncio.run(run())[0]
    assert result["status"] == "error"
    assert "FileNotFoundError" in result["error"]
    assert "uvx not found" in result["error"]
