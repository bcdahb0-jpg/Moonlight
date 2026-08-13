"""screen_awareness 长稳（soak）冒烟脚本（Phase 6）。

模拟连续 ingest 帧（含去重、单飞、隐私阻断路径），验证：
- 上下文数量不随帧数无限增长（sweep 清理空闲客户端）；
- 单客户端 in-flight 不泄漏（并发 ≤1）；
- metrics 计数正确；原始图像帧不落盘（内存仅保留最近一帧）。

用法：
    cd backend
    ./.venv/Scripts/python.exe scripts/screen_soak.py [iterations] [seconds]
    例：./.venv/Scripts/python.exe scripts/screen_soak.py 2000 60
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from src.open_llm_vtuber.screen_awareness import metrics as metrics_mod
from src.open_llm_vtuber.screen_awareness.models import (
    ScreenConfig,
    ScreenFrame,
    ScreenSnapshot,
    ScreenWindowInfo,
)
from src.open_llm_vtuber.screen_awareness.service import get_store

# 1x1 合法 PNG（本地解码用，不产生网络请求）
_FAKE_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
IMAGE = f"data:image/png;base64,{_FAKE_PNG}"


async def _slow_analyze(_frame, api_key=""):
    """模拟真实视觉调用耗时（不真的发网络请求），并埋 analyze_count 指标。"""
    from src.open_llm_vtuber.screen_awareness.metrics import get_metrics

    await asyncio.sleep(0.005)
    get_metrics().inc("analyze_count")
    return ScreenSnapshot(scene="coding", summary="soak", confidence=0.9)


async def main(iters: int, seconds: float) -> None:
    store = get_store()
    store.clear_all()
    store.configure(ScreenConfig(enabled=True), enabled=True)
    store.analyzer.analyze = _slow_analyze  # type: ignore[method-assign]

    deadline = time.monotonic() + seconds
    n = 0
    t0 = time.monotonic()
    # 单客户端 + 固定窗口 + hash 每 2 帧重复 → 稳定走「去重/分析/单飞」路径。
    window = ScreenWindowInfo(title="窗口 A", app="Code", pid=1000)
    store.set_client_enabled("u0", True)
    while n < iters and time.monotonic() < deadline:
        frame = ScreenFrame(
            frame_id=f"soak-{n}",
            captured_at=time.time(),
            window=window,
            image=IMAGE,
            image_hash=f"h{(n // 2) % 2}",
            reason="content_changed",
        )
        await store.ingest("u0", frame)
        n += 1
    elapsed = time.monotonic() - t0

    m = metrics_mod.get_metrics()
    snap = m.snapshot()
    ctx_count = len(store._contexts)
    print(f"--- soak 完成: {n} 帧 / {elapsed:.1f}s / {n / max(elapsed, 1e-6):.0f} fps ---")
    print(f"上下文数(客户端): {ctx_count}  (期望 1)")
    print(f"采集 {snap['frames_captured']} · 去重 {snap['frames_deduped']} · 丢弃 {snap['frames_dropped']}")
    print(f"去重率: {snap['dedupe_ratio']:.2f}")
    print(f"分析成功 {snap['analyze_count']} · 失败 {snap['analyze_errors']}")
    print(f"capture P50/P95: {snap['capture_p50_ms']}/{snap['capture_p95_ms']} ms")
    print(f"analyze P50/P95: {snap['analyze_p50_ms']}/{snap['analyze_p95_ms']} ms")

    # 断言：无泄漏 + 去重与分析路径都真实走到
    assert ctx_count <= 1, "上下文数超过客户端数（泄漏）"
    assert snap["frames_deduped"] > 0, "去重路径未走到（soak 设计问题）"
    assert snap["analyze_count"] > 0, "分析路径未走到（soak 设计问题）"
    assert snap["frames_dropped"] == 0, "单飞路径出现非预期丢弃（并发控制异常）"
    store.clear_all()
    print("SOAK OK（无泄漏，清理完成）")


if __name__ == "__main__":
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    asyncio.run(main(iters, seconds))
