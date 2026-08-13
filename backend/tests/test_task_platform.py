"""任务平台 Phase 1 测试（CRUD + JSONL 会话 + 配置桥）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_platform.py -q
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from src.open_llm_vtuber.task_platform import models, session
from src.open_llm_vtuber.task_platform.conf_bridge import task_config


class TaskPlatformBase(unittest.TestCase):
    def setUp(self):
        # 隔离数据库：指向临时目录，避免污染 backend/task_platform.db
        self.tmp = Path(tempfile.mkdtemp(prefix="ml-task-test-"))
        self.db = self.tmp / "test.db"
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        self._orig_cached = cb._cached
        cb._cached = cb.TaskPlatformConfig(db_path=str(self.db))
        models.init_db()

    def tearDown(self):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        cb._cached = self._orig_cached
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTaskCrud(TaskPlatformBase):
    def test_create_get_list(self):
        t = models.create_task(title="整理目录", workspace=str(self.tmp / "w1"), goal="按类型整理")
        self.assertEqual(t.status, "active")
        self.assertTrue(t.created_at)
        got = models.get_task(t.id)
        self.assertEqual(got.title, "整理目录")
        self.assertEqual(got.goal, "按类型整理")
        lst = models.list_tasks()
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0].id, t.id)

    def test_update_task(self):
        t = models.create_task(title="t1", workspace=str(self.tmp / "w1"))
        up = models.update_task(t.id, {"goal": "新目标", "status": "paused"})
        self.assertEqual(up.goal, "新目标")
        self.assertEqual(up.status, "paused")
        self.assertGreater(up.updated_at, t.created_at)

    def test_delete_task_cascades(self):
        t = models.create_task(title="t1", workspace=str(self.tmp / "w1"))
        r = models.create_run(t.id)
        models.finish_run(r.id, status="completed")
        self.assertTrue(models.delete_task(t.id))
        self.assertIsNone(models.get_task(t.id))
        self.assertEqual(models.list_runs(t.id), [])  # CASCADE

    def test_workspace_preserved_on_delete(self):
        # 删任务不删工作目录
        ws = self.tmp / "keep-me"
        ws.mkdir(parents=True, exist_ok=True)
        t = models.create_task(title="t1", workspace=str(ws))
        models.delete_task(t.id)
        self.assertTrue(ws.exists())


class TestRuns(TaskPlatformBase):
    def test_run_lifecycle(self):
        t = models.create_task(title="t1", workspace=str(self.tmp / "w1"))
        r = models.create_run(t.id)
        self.assertEqual(r.status, "running")
        models.finish_run(r.id, status="completed", summary="搞定")
        got = models.get_run(r.id)
        self.assertEqual(got.status, "completed")
        self.assertEqual(got.summary, "搞定")
        self.assertIsNotNone(got.ended_at)
        # last_run_id 回写到 task
        self.assertEqual(models.get_task(t.id).last_run_id, r.id)
        self.assertEqual(models.get_task(t.id).status, "completed")

    def test_new_run_reactivates_completed_task(self):
        t = models.create_task(title="t1", workspace=str(self.tmp / "w1"))
        first = models.create_run(t.id)
        models.finish_run(first.id, status="completed")
        self.assertEqual(models.get_task(t.id).status, "completed")
        second = models.create_run(t.id)
        self.assertEqual(models.get_task(t.id).status, "active")
        models.finish_run(second.id, status="interrupted")
        self.assertEqual(models.get_task(t.id).status, "active")


class TestSessionJsonl(TaskPlatformBase):
    def test_session_append_and_read(self):
        ws = str(self.tmp / "w1")
        s = session.SessionFile(ws, "task-abc")
        path = s.ensure()
        self.assertTrue(path.exists())
        s.add_message({"role": "user", "content": "你好"}, parent_id=None)
        s.add_message(
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            parent_id="m",
        )
        msgs = s.read_messages()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["message"]["content"], "你好")

    def test_seq_monotonic_across_instances(self):
        ws = str(self.tmp / "w1")
        models.create_task(title="seq", workspace=ws, task_id="task-seq")
        a = session.SessionFile(ws, "task-seq")
        a.ensure()
        seqs = []
        for i in range(2):
            seq = a.next_seq()
            ev = models.TaskEvent(
                seq=seq,
                event_type="status",
                task_id="task-seq",
                run_id="r1",
                payload={"state": "running"},
                created_at="2026-08-08T00:00:00Z",
            )
            models.append_event(ev)  # SQLite 权威
            a.add_event(ev)  # JSONL 兜底
            seqs.append(seq)
        # 新实例接续：从已持久化的最大 seq 继续（不重复不跳号）
        b = session.SessionFile(ws, "task-seq")
        b.ensure()
        seq3 = b.next_seq()
        self.assertEqual((seqs[0], seqs[1], seq3), (1, 2, 3))

    def test_bad_line_skipped(self):
        ws = str(self.tmp / "w1")
        s = session.SessionFile(ws, "task-bad")
        path = s.ensure()
        with open(path, "a", encoding="utf-8") as f:
            f.write("{bad json line\n")
            f.write('{"type":"message","id":"ok","message":{"role":"user","content":"x"}}\n')
        msgs = s.read_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["message"]["content"], "x")


class TestConfBridge(unittest.TestCase):
    def test_task_config_defaults(self):
        cfg = task_config(force_reload=True)
        self.assertEqual(cfg.db_path, "task_platform.db")
        self.assertEqual(cfg.tasks_root, "tasks/")
        self.assertEqual(cfg.max_no_progress, 5)
        # LLM 快照应读到 conf.yaml 的 openai_compatible_llm（有值时才断言）
        if cfg.llm_model:
            self.assertIn("deepseek", cfg.llm_model.lower())


class TestLegacyMigration(unittest.TestCase):
    """Phase 5 迁移：旧库 task_events 无 run_id/category/origin 列，init_db 后补列并回退读。"""

    def _point_at(self, db: Path):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        self._orig_cached = cb._cached
        cb._cached = cb.TaskPlatformConfig(db_path=str(db))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ml-mig-test-"))
        self.db = self.tmp / "test.db"
        self._point_at(self.db)
        # 手工建「旧版」schema：task_events 无 run_id/category/origin
        import sqlite3

        conn = sqlite3.connect(str(self.db))
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, workspace TEXT NOT NULL,
                goal TEXT DEFAULT '', status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_run_id TEXT
            );
            CREATE TABLE task_events (
                task_id TEXT NOT NULL, seq INTEGER NOT NULL,
                event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (task_id, seq)
            );
            """
        )
        # 旧版事件行（payload 是 JSON 文本）
        import json

        conn.execute(
            "INSERT INTO task_events (task_id, seq, event_type, payload, created_at) VALUES (?,?,?,?,?)",
            (
                "t-legacy",
                1,
                "message",
                json.dumps({"role": "assistant", "content": "旧事件"}),
                "2026-01-01T00:00:00.000Z",
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        import src.open_llm_vtuber.task_platform.conf_bridge as cb

        cb._cached = self._orig_cached
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_db_adds_columns_and_replays_legacy_row(self):
        models.init_db()
        evs = models.events_after("t-legacy", 0)
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        # 迁移前旧行：run_id/category/origin 回退为默认值
        self.assertEqual(ev.run_id, "t-legacy")
        self.assertEqual(ev.category, "message")
        self.assertEqual(ev.origin, "core")
        self.assertEqual(ev.payload["content"], "旧事件")

    def test_new_row_persists_run_id_after_migration(self):
        models.init_db()
        ev = models.append_event(
            models.TaskEvent(
                seq=1,
                event_type="tool_call",
                task_id="t-new",
                run_id="r-abc",
                category="trace",
                origin="core",
                payload={"name": "bash", "arguments": {"command": "pwd"}, "tool_call_id": "c1"},
            )
        )
        back = models.events_after("t-new", 0)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].run_id, "r-abc")
        self.assertEqual(back[0].category, "trace")
        self.assertEqual(back[0].origin, "core")

    def test_ensure_column_rejects_unknown_table(self):
        conn = models.get_conn()
        try:
            with self.assertRaises(ValueError):
                models._ensure_column(conn, "users", "some_col", "some_col TEXT")
        finally:
            conn.close()


class TestTaskConversationBridge(TaskPlatformBase):
    """P2 上下文桥：conversation_uid 绑定 + 旧库迁移。"""

    def test_create_task_binds_conversation_uid(self):
        t = models.create_task(
            title="重构接口",
            workspace=str(self.tmp / "w1"),
            goal="重构 utils 接口",
            conversation_uid="conv-123",
        )
        self.assertEqual(t.conversation_uid, "conv-123")
        got = models.get_task(t.id)
        self.assertEqual(got.conversation_uid, "conv-123")
        lst = models.list_tasks()
        self.assertEqual(lst[0].conversation_uid, "conv-123")

    def test_conversation_uid_optional_for_old_tasks(self):
        t = models.create_task(title="旧任务", workspace=str(self.tmp / "w2"))
        self.assertIsNone(t.conversation_uid)
        got = models.get_task(t.id)
        self.assertIsNone(got.conversation_uid)


if __name__ == "__main__":
    unittest.main()
