"""端到端冒烟：memory_v2 路由层 + dream 手动触发 + 固化闭环。

在完全隔离的临时目录内进行（最小 conf.yaml + 独立 chat_history），
测完整个目录删除，绝不触碰真实配置与记忆数据。

运行：cd backend && PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 \
       .venv/Scripts/python.exe tests/e2e_memory_v2.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile

# 先把 backend/src 绝对路径固定下来再 chdir（abspath 依賴 cwd，順序不能反）
_BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
sys.path.insert(0, os.path.join(_BACKEND, "src"))

_TMP = tempfile.mkdtemp(prefix="e2e_memory_v2_")
os.chdir(_TMP)

# 最小 conf.yaml（memory_route 的宽松读取即可通过；LLM 留空 → dream 跳过 LLM 步骤）
with open("conf.yaml", "w", encoding="utf-8") as f:
    f.write(
        "character_config:\n"
        "  conf_uid: test_e2e\n"
        "  conf_name: 測試\n"
        "  persona_prompt: 測試\n"
        "  live2d_model_name: x\n"
        "  character_name: 測試\n"
        "  human_name: 使用者\n"
        "  avatar: ''\n"
        "  agent_config:\n"
        "    llm_configs:\n"
        "      openai_compatible_llm:\n"
        "        base_url: ''\n"
        "        model: ''\n"
        "        llm_api_key: ''\n"
    )

from fastapi import FastAPI
import httpx

from open_llm_vtuber import memory_v2
from open_llm_vtuber.memory_route import init_memory_route

UID = "test_e2e"


def main():
    # ---- 造数据：2 条同 hash facts + 高证据 confirmed reflection + 1 条 pending proposal
    f1 = memory_v2.upsert_fact(UID, "使用者喜歡貓", importance=8)
    memory_v2.upsert_fact(UID, "使用者養了三隻貓", importance=7)
    # 直接 SQL 插一条同 hash 的历史遗留重复行（测确定性合并）
    conn = memory_v2._open(UID)
    h = memory_v2.canonical_hash("使用者喜歡貓")
    conn.execute(
        "INSERT INTO facts(text, importance, entity, source, canonical_hash, "
        "created_at, last_seen_at, status) VALUES (?, 3.0, 'user', 'legacy_dup', "
        "?, 1000.0, 1000.0, 'active')",
        ("使用者喜歡貓（重複）", h),
    )
    conn.commit()
    conn.close()
    # confirmed reflection：f1 + 另一条 fact 支撑，打够证据分
    f2 = memory_v2.upsert_fact(UID, "使用者每天都鏟貓砂", importance=6)
    memory_v2.add_reflection(UID, "使用者是資深貓奴", [f1, f2])
    for _ in range(3):
        memory_v2.apply_signal(UID, f1, "reinforce")
        memory_v2.apply_signal(UID, f2, "reinforce")
    memory_v2.refresh_statuses(UID)
    assert memory_v2.list_reflections(UID)[0]["status"] == "confirmed"
    memory_v2._create_proposal(UID, "conflict", [f1, f2], "", 0.8)

    # ---- FastAPI app + httpx ASGITransport（client host = 127.0.0.1 通过 localhost 守卫）
    app = FastAPI()
    app.include_router(init_memory_route())
    transport = httpx.ASGITransport(app=app)

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            # 1) GET /api/memory 含 v2 状态
            r = await c.get("/api/memory", params={"conf_uid": UID})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["v2_enabled"] is True
            assert d["v2_max_facts"] == 500
            assert d["v2_facts"] >= 4
            assert d["v2_reflections"] >= 1
            assert d["v2_proposals_pending"] >= 1
            print("[GET /api/memory] v2 fields OK:", {k: d[k] for k in
                  ("v2_enabled", "v2_facts", "v2_reflections", "v2_proposals_pending")})

            # 2) POST /api/memory/dream（LLM 空 → 只跑确定性步骤 + 固化）
            r = await c.post("/api/memory/dream",
                             json={"conf_uid": UID, "promote_min_age_days": 0})
            assert r.status_code == 200, r.text
            s = r.json()["summary"]
            print("[POST /api/memory/dream] summary:", s)
            assert s["hash_dedup"] >= 1
            assert s["reflections_fused"] >= 1

            # 3) core_memory.md 闭环写入
            from open_llm_vtuber import memory_core
            core = memory_core.load_core_memory(UID)
            assert "貓奴" in core, core
            print("[core_memory.md] fused OK:", core)
            # reflection 已 promoted（幂等）
            assert memory_v2.list_reflections(UID)[0]["status"] == "promoted"

            # 4) 检索接口
            r = await c.get("/api/memory/v2/facts", params={"conf_uid": UID, "limit": 5})
            assert r.status_code == 200 and len(r.json()["facts"]) >= 1
            r = await c.get("/api/memory/v2/reflections", params={"conf_uid": UID})
            assert r.status_code == 200 and len(r.json()["reflections"]) >= 1
            r = await c.get("/api/memory/v2/proposals", params={"conf_uid": UID})
            assert r.status_code == 200 and len(r.json()["proposals"]) >= 1
            print("[GET /api/memory/v2/*] list endpoints OK")

            # 5) 提案审批（拒绝）
            pid = r.json()["proposals"][0]["id"]
            r = await c.post(f"/api/memory/v2/proposals/{pid}",
                             json={"conf_uid": UID, "action": "reject"})
            assert r.status_code == 200 and r.json()["ok"], r.text
            # 再批一次 → 已决定报错
            r = await c.post(f"/api/memory/v2/proposals/{pid}",
                             json={"conf_uid": UID, "action": "reject"})
            assert r.status_code == 400
            print("[POST /api/memory/v2/proposals/{id}] lifecycle OK")

            # 6) 信号接口
            r = await c.post("/api/memory/v2/facts/signal",
                             json={"conf_uid": UID, "fact_id": f1, "action": "reinforce"})
            assert r.status_code == 200 and r.json()["ok"]
            print("[POST /api/memory/v2/facts/signal] OK")

    asyncio.run(run())
    print("E2E ALL PASS")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
