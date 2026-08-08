"""记忆 v2 全链路实测（真实 LLM + 独立测试角色）。

走真实数据路径：模拟 6 轮对话 → post_turn 提取 facts（DeepSeek 真实调用）→
合成 reflection → 证据信号确认 → dreaming 合并（LLM 批量判定）→ 固化 core_memory.md
→ 检索命中。测完删除测试角色目录，不污染真实数据。

运行：cd backend && PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 \
       .venv/Scripts/python.exe tests/e2e_memory_v2_live.py
"""
import asyncio
import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")))

from open_llm_vtuber import memory_core, memory_v2
from open_llm_vtuber.config_manager.utils import read_yaml

UID = "e2e_live"  # 独立测试角色，测完删除
CHAT_DIR = os.path.join("chat_history", UID)

TURNS = [
    ("我叫小明，是一名前端工程师，主要写 React。", "你好小明，很高兴认识你！"),
    ("我每天早上六点都会去跑步五公里。", "坚持晨跑真的很厉害！"),
    ("我养了三只猫，最疼的那只叫咪咪。", "三只猫的猫奴，辛苦了！"),
    ("我的工作就是写前端页面，用 React 技术栈，已经做了五年。", "资深前端了！"),
    ("最近在自学日语，目标是年底考过 N2。", "学语言很需要毅力，加油！"),
    ("我特别讨厌香菜，闻到味道就不行。", "哈哈，香菜党狂怒！"),
]


def llm_conf():
    data = read_yaml("conf.yaml") or {}
    llm = (
        data.get("character_config", {})
        .get("agent_config", {})
        .get("llm_configs", {})
        .get("openai_compatible_llm", {})
    ) or {}
    return str(llm.get("base_url") or ""), str(llm.get("model") or ""), str(llm.get("llm_api_key") or "")


def main():
    base_url, model, api_key = llm_conf()
    print(f"LLM: {model} @ {base_url}")
    assert base_url and model, "conf.yaml 里没有可用的 openai_compatible_llm 配置"

    # ---- 1. 模拟 6 轮对话，走 post_turn 真实提取 ----
    for i, (user, ai) in enumerate(TURNS, 1):
        r = asyncio.run(
            memory_v2.post_turn(UID, user, ai, base_url, model, api_key, auto_dream=False)
        )
        print(f"[turn {i}] facts+{r['facts']} signals+{r['signals']} reflections+{r['reflections']}")

    facts = memory_v2.list_facts(UID, status="active")
    print(f"\n[check] 提取到 active facts: {len(facts)}")
    for f in facts:
        print(f"  #{f['id']} imp={f['importance']} {f['text']}")
    assert len(facts) >= 5, "提取不足 5 条，链路第一步失败"

    # ---- 2. 显式合成反思（真实 LLM；post_turn 内部可能已合成过） ----
    n = asyncio.run(memory_v2.synthesize_reflections(UID, base_url, model, api_key))
    print(f"[check] 补充合成 reflections: {n}")
    refs = memory_v2.list_reflections(UID)
    print(f"[check] reflections 总数: {len(refs)}")
    for r in refs:
        print(f"  #{r['id']} [{r['status']}] {r['text']}")
    assert len(refs) >= 1, "反思合成失败"

    # ---- 3. 证据确认：每条 fact 反复强化 → 状态机推进 ----
    for f in memory_v2.list_facts(UID, status="active"):
        for _ in range(3):
            memory_v2.apply_signal(UID, f["id"], "reinforce")
    changed = memory_v2.refresh_statuses(UID)
    refs = memory_v2.list_reflections(UID)
    print(f"[check] refresh_statuses 改动 {changed} 条，reflection 状态: {[r['status'] for r in refs]}")
    assert any(r["status"] in ("confirmed", "promoted") for r in refs), "reflection 未达 confirmed"

    # ---- 4. dreaming 合并（真实 LLM 批量判定 + 固化） ----
    summary = asyncio.run(
        memory_v2.run_dream_pass(UID, base_url, model, api_key, promote_min_age_days=0)
    )
    print(f"\n[dream] summary: {summary}")
    # 第 1/4 轮是语义重复事实（前端 + React），LLM 判定应至少产生一次 merge 应用或提案
    assert summary["auto_merged"] >= 1 or summary["proposals"] >= 1, (
        "dream LLM 批量判定未生效（期望 merge/提案）"
    )

    # ---- 5. 固化断言：core_memory.md 出现反思 ----
    core = memory_core.load_core_memory(UID)
    print(f"[check] core_memory.md ({len(core)} 字):\n{core}")
    assert core.strip(), "core_memory.md 为空"
    if summary["reflections_fused"] >= 1:
        assert any(r["text"] in core for r in refs), "固化内容未写入 core_memory.md"

    # ---- 6. 检索命中（FTS + LIKE 双通道） ----
    hits = memory_v2.search(UID, "我喜歡貓咪", k=3)
    print(f"\n[check] 检索「我喜歡貓咪」→ {len(hits)} 个块")
    for h in hits:
        print("  ", h.replace("\n", " / ")[:120])
    hits2 = memory_v2.search(UID, "跑步習慣", k=3)
    print(f"[check] 检索「跑步習慣」→ {len(hits2)} 个块（验证 2 字词 LIKE 兜底）")
    assert any("跑步" in h for h in hits2) or any("跑步" in h for h in hits), "检索未命中"

    # ---- 7. 提案 / 状态总览 ----
    st = memory_v2.stats(UID)
    print(f"\n[stats] {st}")
    print("\n== 全链路测试 PASS ==")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(CHAT_DIR, ignore_errors=True)
        print(f"[cleanup] 已删除测试角色目录 {CHAT_DIR}")
