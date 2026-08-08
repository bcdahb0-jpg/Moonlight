"""memory_v2（记忆类型化 + 睡眠合并）单元测试。

覆盖计划书 §3.1-§3.4 的核心路径：
- 阶段 1：facts 写入 + canonical_hash 去重 + 数量上限
- 阶段 2：证据双时钟半衰期衰减 + 状态机 pending → confirmed / archived
- 阶段 3：hash 确定性合并、提案创建/决定、固化闭环（写入 core_memory.md）+ 幂等
- 阶段 4：FTS5 trigram 检索（facts/reflections）

运行：uv run python -m unittest tests.test_memory_v2 -v
      或  python -m unittest discover -s tests -p "test_memory_v2.py" -v
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest

# 隔离 chat_history：所有测试在临时目录内进行，绝不碰真实记忆数据
_TMP = tempfile.mkdtemp(prefix="memory_v2_test_")
os.chdir(_TMP)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.open_llm_vtuber import memory_core, memory_v2 as m


class TestEvidenceMath(unittest.TestCase):
    """纯函数：半衰期衰减 + evidence_score + 状态机（计划书 §3.3）。"""

    def test_reinforcement_no_clock_no_decay(self):
        self.assertAlmostEqual(m.effective_reinforcement(1.0, None, 1e6), 1.0)

    def test_reinforcement_halflife_decay(self):
        now = 1e6
        last = now - 30 * 86400  # 30 天前
        self.assertAlmostEqual(m.effective_reinforcement(1.0, last, now), 0.5, places=6)

    def test_importance_decay(self):
        now = 1e6
        self.assertAlmostEqual(
            m.decayed_importance(8.0, now - 30 * 86400, now), 4.0, places=6
        )

    def test_evidence_score_rein_minus_disp(self):
        self.assertEqual(m.evidence_score(2.0, 1.0, None, None, 1e6), 1.0)

    def test_status_machine(self):
        self.assertEqual(m._derive_reflection_status("pending", 2.0, 1.0), "confirmed")
        self.assertEqual(m._derive_reflection_status("pending", -0.8, 1.0), "archived")
        # promoted 不往回退
        self.assertEqual(m._derive_reflection_status("promoted", -1.0, 1.0), "promoted")

    def test_canonical_hash_ignores_whitespace(self):
        self.assertEqual(m.canonical_hash("我 喜歡 貓"), m.canonical_hash("我喜歡貓"))


class TestFacts(unittest.TestCase):
    """阶段 1：facts 提取与存储（计划书 §3.1/§3.2）。"""

    def setUp(self):
        self.uid = "t_facts"

    def test_upsert_dedup_by_hash(self):
        f1 = m.upsert_fact(self.uid, "我 喜歡 貓", importance=6)
        f2 = m.upsert_fact(self.uid, "我喜歡貓", importance=7)
        self.assertEqual(f1, f2)  # 同 hash → 同一行，更新 importance 取 max
        rows = [f for f in m.list_facts(self.uid) if f["id"] == f1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["importance"], 7.0)

    def test_upsert_max_facts_crop(self):
        for i in range(12):
            m.upsert_fact(self.uid, f"測試事實第{i}號", importance=float(i), max_facts=10)
        self.assertLessEqual(m.count_facts(self.uid), 10)

    def test_empty_or_oversize_text_guarded(self):
        self.assertIsNone(m.upsert_fact(self.uid, "   "))
        long_text = "很長" * 500
        fid = m.upsert_fact(self.uid, long_text)
        self.assertIsNotNone(fid)
        rows = [f for f in m.list_facts(self.uid) if f["id"] == fid]
        self.assertLessEqual(len(rows[0]["text"]), 301)


class TestEvidenceSignals(unittest.TestCase):
    """阶段 2：证据双时钟（计划书 §3.2.3/§3.3）。"""

    def setUp(self):
        self.uid = "t_signals"

    def test_signal_updates_own_clock_only(self):
        fid = m.upsert_fact(self.uid, "使用者每天跑步", importance=5)
        self.assertTrue(m.apply_signal(self.uid, fid, "reinforce"))
        self.assertFalse(m.apply_signal(self.uid, fid, "bogus"))
        rows = {f["id"]: f for f in m.list_facts(self.uid)}
        self.assertEqual(rows[fid]["reinforcement"], 1.0)
        self.assertEqual(rows[fid]["disputation"], 0.0)
        m.apply_signal(self.uid, fid, "rebut")
        rows = {f["id"]: f for f in m.list_facts(self.uid)}
        self.assertEqual(rows[fid]["disputation"], 1.0)

    def test_reflection_score_derived_from_source_facts(self):
        f1 = m.upsert_fact(self.uid, "使用者喜歡貓", importance=6)
        f2 = m.upsert_fact(self.uid, "使用者養了三隻貓", importance=5)
        m.add_reflection(self.uid, "使用者是貓奴", [f1, f2])
        # 未打信号 → 0 分 → pending
        m.refresh_statuses(self.uid)
        self.assertEqual(m.list_reflections(self.uid)[0]["status"], "pending")
        # 打够证据 → confirmed
        for _ in range(2):
            m.apply_signal(self.uid, f1, "reinforce")
            m.apply_signal(self.uid, f2, "reinforce")
        m.refresh_statuses(self.uid)
        self.assertEqual(m.list_reflections(self.uid)[0]["status"], "confirmed")

    def test_decayed_fact_archived(self):
        # 造一条 100 天前、importance=1 的 fact → 衰減後遠低於門檻
        conn = m._open(self.uid)
        conn.execute(
            "INSERT INTO facts(text, importance, entity, source, canonical_hash, "
            "created_at, last_seen_at, status) VALUES (?, ?, 'user', 'llm_extract', "
            "?, ?, ?, 'active')",
            ("很久以前的小事", 1.0, m.canonical_hash("很久以前的小事"),
             _OLD_TS := 1e9 - 100 * 86400, _OLD_TS),
        )
        conn.commit()
        conn.close()
        n = m._archive_decayed(self.uid)
        self.assertEqual(n, 1)


class TestDreaming(unittest.TestCase):
    """阶段 3：dreaming 合并 + 固化闭环（计划书 §3.4）。"""

    def setUp(self):
        self.uid = "t_dream"

    def test_hash_dedup_keeps_newest(self):
        f1 = m.upsert_fact(self.uid, "使用者喜歡貓", importance=6)
        # 直接插一条同 hash 的历史遗留重复行
        conn = m._open(self.uid)
        h = m.canonical_hash("使用者喜歡貓")
        conn.execute(
            "INSERT INTO facts(text, importance, entity, source, canonical_hash, "
            "created_at, last_seen_at, status) VALUES (?, 3.0, 'user', 'legacy_dup', "
            "?, 1000.0, 1000.0, 'active')",
            ("使用者喜歡貓（重複）", h),
        )
        conn.commit()
        conn.close()
        n = m._dedup_by_hash(self.uid)
        self.assertGreaterEqual(n, 1)
        active = m.list_facts(self.uid, status="active")
        self.assertTrue(any(f["id"] == f1 for f in active))

    def test_proposal_lifecycle(self):
        f1 = m.upsert_fact(self.uid, "使用者喜歡貓", importance=6)
        f2 = m.upsert_fact(self.uid, "使用者討厭貓", importance=6)
        pid = m._create_proposal(self.uid, "conflict", [f1, f2], "", 0.8)
        self.assertIsNotNone(pid)
        self.assertEqual(m.count_proposals(self.uid, "pending"), 1)
        ok, err = m.decide_proposal(self.uid, pid, False)  # 拒绝
        self.assertTrue(ok and not err)
        ok, err = m.decide_proposal(self.uid, pid, False)  # 已决定 → 拒绝
        self.assertFalse(ok)

    def test_fuse_into_core_memory_closed_loop_and_idempotent(self):
        f1 = m.upsert_fact(self.uid, "使用者喜歡貓", importance=8)
        f2 = m.upsert_fact(self.uid, "使用者養了三隻貓", importance=7)
        m.add_reflection(self.uid, "使用者是資深貓奴", [f1, f2])
        for _ in range(3):
            m.apply_signal(self.uid, f1, "reinforce")
            m.apply_signal(self.uid, f2, "reinforce")
        m.refresh_statuses(self.uid)
        self.assertEqual(m.list_reflections(self.uid)[0]["status"], "confirmed")

        promotable = m._promotable_reflections(self.uid, min_age_days=0)
        self.assertGreaterEqual(len(promotable), 1)
        fused = m._fuse_into_core_memory(self.uid, promotable, cap=1500)
        self.assertGreaterEqual(fused, 1)
        core = memory_core.load_core_memory(self.uid)
        self.assertIn("資深貓奴", core)
        # reflection 固化一次後 promoted，不再重複寫
        self.assertEqual(m.list_reflections(self.uid)[0]["status"], "promoted")
        again = m._fuse_into_core_memory(
            self.uid, m._promotable_reflections(self.uid, min_age_days=0), cap=1500
        )
        self.assertEqual(again, 0)


class TestSearch(unittest.TestCase):
    """阶段 4：facts/reflections 納入 FTS 檢索池。"""

    def setUp(self):
        self.uid = "t_search"

    def test_search_hits_fact_and_reflection(self):
        m.upsert_fact(self.uid, "使用者喜歡貓", importance=6)
        m.upsert_fact(self.uid, "使用者每天跑步五公里", importance=5)
        m.add_reflection(self.uid, "使用者生活規律且愛運動", [])
        hits = m.search(self.uid, "我喜歡貓咪", k=3)
        self.assertTrue(any("喜歡貓" in h for h in hits))
        hits2 = m.search(self.uid, "跑步習慣", k=3)
        self.assertTrue(any("跑步" in h for h in hits2))

    def test_search_short_query_no_crash(self):
        self.assertEqual(m.search(self.uid, "喵", k=3), [])

    def test_stats_fail_soft(self):
        st = m.stats("nonexistent_char_xyz")
        self.assertIsInstance(st, dict)
        self.assertEqual(st["exists"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
