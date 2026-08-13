"""screen_awareness 单元测试：协议模型、隐私规则、去重/TTL/单飞、策略、指标、解析。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_screen_awareness.py -q --basetemp=.pytest-tmp
"""
import base64
import time
import unittest

# 1x1 红色 PNG（假图像夹具，2.5ms 可解码，绝不调外部服务）。
_FAKE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)
FAKE_IMAGE = f"data:image/png;base64,{_FAKE_PNG_B64}"


def make_frame(
    title: str = "VS Code - main.py",
    app: str = "Code",
    pid: int = 1234,
    image: str = FAKE_IMAGE,
    image_hash: str = "hash-a",
    reason: str = "content_changed",
) -> dict:
    return {
        "type": "screen-frame",
        "frame_id": f"f-{pid}-{image_hash}-{int(time.time() * 1000)}",
        "captured_at": time.time(),
        "window": {"title": title, "app": app, "pid": pid, "bounds": [0, 0, 1920, 1080]},
        "image": image,
        "image_hash": image_hash,
        "reason": reason,
    }


class TestModels(unittest.TestCase):
    def test_frame_validation(self):
        from src.open_llm_vtuber.screen_awareness.models import ScreenFrame

        f = ScreenFrame.model_validate(make_frame())
        self.assertEqual(f.type, "screen-frame")
        self.assertEqual(f.window.pid, 1234)
        self.assertEqual(f.reason, "content_changed")

    def test_missing_required_rejected(self):
        from pydantic import ValidationError
        from src.open_llm_vtuber.screen_awareness.models import ScreenFrame

        with self.assertRaises(ValidationError):
            ScreenFrame.model_validate({"type": "screen-frame"})

    def test_snapshot_defaults(self):
        from src.open_llm_vtuber.screen_awareness.models import ScreenSnapshot

        s = ScreenSnapshot()
        self.assertEqual(s.scene, "unknown")
        self.assertFalse(s.sensitive)
        self.assertEqual(s.confidence, 0.0)

    def test_screen_config_from_missing(self):
        from src.open_llm_vtuber.screen_awareness.models import screen_config_from

        class _Cfg:
            pass

        cfg = screen_config_from(_Cfg())
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_side, 1280)

    def test_screen_config_from_partial(self):
        from src.open_llm_vtuber.screen_awareness.models import screen_config_from

        class _Cfg:
            screen_awareness = {"enabled": True, "quality": 80, "junk": 1}

        cfg = screen_config_from(_Cfg())
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.quality, 80)  # 白名单外的 junk 被忽略


class TestPrivacy(unittest.TestCase):
    def _policy(self, **kw):
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig
        from src.open_llm_vtuber.screen_awareness.privacy import PrivacyPolicy

        return PrivacyPolicy(ScreenConfig(**kw))

    def _win(self, title="hi", app="Code", pid=1):
        from src.open_llm_vtuber.screen_awareness.models import ScreenWindowInfo

        return ScreenWindowInfo(title=title, app=app, pid=pid)

    def test_normal_window_allowed(self):
        p = self._policy()
        blocked, reason = p.check(self._win("VS Code - main.py", "Code"))
        self.assertFalse(blocked)

    def test_moonlight_self_blocked(self):
        p = self._policy()
        blocked, reason = p.check(self._win("Moonlight", "moonlight"))
        self.assertTrue(blocked)
        self.assertEqual(reason, "moonlight_self")

    def test_sensitive_app_blocked(self):
        p = self._policy()
        blocked, reason = p.check(self._win("vault", "Bitwarden"))
        self.assertTrue(blocked)
        self.assertEqual(reason, "sensitive_app")

    def test_password_title_blocked(self):
        p = self._policy()
        blocked, reason = p.check(self._win("登录 - 支付中心", "chrome"))
        self.assertTrue(blocked)
        self.assertIn(reason, ("title_keyword",))

    def test_user_blocklist_app(self):
        p = self._policy(blocked_apps=["wechat", "qq"])
        blocked, reason = p.check(self._win("聊天", "WeChat"))
        self.assertTrue(blocked)
        self.assertEqual(reason, "user_blocklist_app")

    def test_user_title_keyword(self):
        p = self._policy(blocked_title_keywords=["工资条"])
        blocked, reason = p.check(self._win("6月工资条.xlsx", "excel"))
        self.assertTrue(blocked)
        self.assertEqual(reason, "user_blocklist_title")

    def test_allowlist_app_bypass(self):
        p = self._policy(allowlist_apps=["code", "visual studio code"])
        blocked, reason = p.check(self._win("密码本.md", "Code"))
        self.assertFalse(blocked)


class TestAnalyzer(unittest.TestCase):
    def test_parse_clean_json(self):
        from src.open_llm_vtuber.screen_awareness.analyzer import _parse_snapshot

        s = _parse_snapshot(
            '{"scene": "coding", "summary": "用户在写代码", "salient_text": ["TS2322"], '
            '"possible_topic": "类型错误", "sensitive": false, '
            '"worth_interrupting": true, "confidence": 0.9}'
        )
        self.assertIsNotNone(s)
        self.assertEqual(s.scene, "coding")
        self.assertEqual(s.salient_text, ["TS2322"])
        self.assertTrue(s.worth_interrupting)

    def test_parse_fenced_json(self):
        from src.open_llm_vtuber.screen_awareness.analyzer import _parse_snapshot

        s = _parse_snapshot('```json\n{"scene": "video", "confidence": 0.5}\n```')
        self.assertIsNotNone(s)
        self.assertEqual(s.scene, "video")

    def test_parse_embedded_json(self):
        from src.open_llm_vtuber.screen_awareness.analyzer import _parse_snapshot

        s = _parse_snapshot('好的，分析结果如下：{"scene": "chat", "confidence": 0.8} 完毕')
        self.assertIsNotNone(s)
        self.assertEqual(s.scene, "chat")

    def test_parse_garbage_returns_none(self):
        from src.open_llm_vtuber.screen_awareness.analyzer import _parse_snapshot

        self.assertIsNone(_parse_snapshot(""))
        self.assertIsNone(_parse_snapshot("我不明白你在说什么"))

    def test_analyze_no_provider_fails_soft(self):
        from src.open_llm_vtuber.screen_awareness.analyzer import VisionAnalyzer
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenFrame

        import asyncio

        analyzer = VisionAnalyzer(ScreenConfig())  # 无 base_url → 直接 None
        snap = asyncio.run(analyzer.analyze(ScreenFrame.model_validate(make_frame())))
        self.assertIsNone(snap)

    def test_analyze_local_only_rejects_remote(self):
        from src.open_llm_vtuber.screen_awareness.analyzer import VisionAnalyzer
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig

        analyzer = VisionAnalyzer(
            ScreenConfig(
                base_url="https://api.example.com/v1",
                model="gpt-4o",
                local_only=True,
            )
        )
        self.assertIsNone(analyzer._endpoint())

    # ---- Phase 6：provider fail-soft（超时/断网/429/500） ---- #

    def _mock_analyze(self, status_code=200, content=None, usage=None, exc=None):
        """构造 mock httpx.AsyncClient.post 的测试环境，返回 (analyzer, frame)。"""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from src.open_llm_vtuber.screen_awareness.analyzer import VisionAnalyzer
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenFrame

        analyzer = VisionAnalyzer(
            ScreenConfig(base_url="http://127.0.0.1:9/v1", model="m", api_key="k")
        )
        frame = ScreenFrame.model_validate(make_frame())

        def _make_resp(code, payload, usage_map):
            class _Resp:
                status_code = code
                content = payload

                def json(self):
                    return {
                        "choices": [{"message": {"content": self.content}}],
                        "usage": usage_map or {},
                    }

                def raise_for_status(self):
                    if self.status_code >= 400:
                        import httpx

                        raise httpx.HTTPStatusError(
                            f"HTTP {self.status_code}", request=None, response=self
                        )

            return _Resp()

        resp = _make_resp(status_code, content, usage)

        async def _post(*_a, **_k):
            if exc is not None:
                raise exc
            return resp

        return asyncio, patch(
            "src.open_llm_vtuber.screen_awareness.analyzer.httpx.AsyncClient.post",
            AsyncMock(side_effect=_post),
        ), analyzer, frame

    def test_analyze_http_500_fails_soft(self):
        import httpx

        asyncio, patcher, analyzer, frame = self._mock_analyze(status_code=500)
        with patcher:
            snap = asyncio.run(analyzer.analyze(frame, api_key="k"))
        self.assertIsNone(snap)

    def test_analyze_http_429_fails_soft(self):
        asyncio, patcher, analyzer, frame = self._mock_analyze(status_code=429)
        with patcher:
            snap = asyncio.run(analyzer.analyze(frame, api_key="k"))
        self.assertIsNone(snap)

    def test_analyze_network_error_fails_soft(self):
        import httpx

        exc = httpx.ConnectError("connection refused")
        asyncio, patcher, analyzer, frame = self._mock_analyze(exc=exc)
        with patcher:
            snap = asyncio.run(analyzer.analyze(frame, api_key="k"))
        self.assertIsNone(snap)

    def test_analyze_timeout_fails_soft(self):
        import httpx

        exc = httpx.TimeoutException("timeout")
        asyncio, patcher, analyzer, frame = self._mock_analyze(exc=exc)
        with patcher:
            snap = asyncio.run(analyzer.analyze(frame, api_key="k"))
        self.assertIsNone(snap)

    def test_analyze_success_counts_input_output_tokens(self):
        from src.open_llm_vtuber.screen_awareness import metrics as metrics_mod

        content = (
            '{"scene": "coding", "summary": "用户在写代码", "salient_text": ["TS2322"], '
            '"possible_topic": "类型错误", "sensitive": false, '
            '"worth_interrupting": true, "confidence": 0.9}'
        )
        metrics_mod.get_metrics().vision_input_tokens = 0
        metrics_mod.get_metrics().vision_output_tokens = 0
        asyncio, patcher, analyzer, frame = self._mock_analyze(
            content=content, usage={"prompt_tokens": 1200, "completion_tokens": 40}
        )
        with patcher:
            snap = asyncio.run(analyzer.analyze(frame, api_key="k"))
        self.assertIsNotNone(snap)
        self.assertEqual(snap.scene, "coding")
        m = metrics_mod.get_metrics()
        self.assertGreaterEqual(m.vision_input_tokens, 1200)
        self.assertGreaterEqual(m.vision_output_tokens, 40)


class TestStore(unittest.TestCase):
    def setUp(self):
        from src.open_llm_vtuber.screen_awareness.service import get_store

        self.store = get_store()
        self.store.clear_all()
        self.store.configure(
            __import__("src.open_llm_vtuber.screen_awareness.models", fromlist=["ScreenConfig"]).ScreenConfig(
                enabled=True, poll_interval_sec=3.0
            ),
            enabled=True,
        )
        self.store.set_client_enabled("u1", True)

    def tearDown(self):
        self.store.clear_all()

    async def _ingest(self, **kw):
        from src.open_llm_vtuber.screen_awareness.models import ScreenFrame

        return await self.store.ingest(
            "u1", ScreenFrame.model_validate(make_frame(**kw)), api_key=""
        )

    def test_ingest_without_provider_keeps_window_meta(self):
        import asyncio

        st = asyncio.run(self._ingest())
        self.assertTrue(st.enabled)
        self.assertEqual(st.last_window_app, "Code")

    def test_dedup_same_hash(self):
        import asyncio
        from src.open_llm_vtuber.screen_awareness import metrics as metrics_mod

        metrics_mod.get_metrics().frames_captured = 0
        metrics_mod.get_metrics().frames_deduped = 0
        asyncio.run(self._ingest(image_hash="h1"))
        asyncio.run(self._ingest(image_hash="h1"))  # 同窗口同 hash → 去重
        m = metrics_mod.get_metrics()
        self.assertEqual(m.frames_captured, 1)
        self.assertEqual(m.frames_deduped, 1)

    def test_window_switch_invalidates(self):
        import asyncio

        asyncio.run(self._ingest(title="VS Code", image_hash="h1"))
        snap1 = self.store.latest_snapshot("u1")
        # 无 provider，snapshot 为 None；但 frame 应保留。
        self.assertIsNone(snap1)
        asyncio.run(self._ingest(title="Chrome", app="chrome", pid=999, image_hash="h2"))
        self.assertEqual(self.store.status("u1").last_window_title, "Chrome")

    def test_clear_removes_all(self):
        import asyncio

        asyncio.run(self._ingest())
        self.store.clear("u1")
        st = self.store.status("u1")
        self.assertEqual(st.last_window_title, "")
        self.assertIsNone(self.store.latest_frame("u1"))

    def test_pause_then_ingest_dropped(self):
        import asyncio
        from src.open_llm_vtuber.screen_awareness import metrics as metrics_mod

        metrics_mod.get_metrics().frames_dropped = 0
        self.store.pause("u1", "user_paused")
        asyncio.run(self._ingest(image_hash="hp"))
        m = metrics_mod.get_metrics()
        self.assertGreaterEqual(m.frames_dropped, 0)  # 未启用 → 不计数也不分析
        self.assertFalse(self.store.status("u1").enabled)

    def test_sweep_removes_idle_context(self):
        """Phase 6：sweep 清理超过 2 分钟无活动的客户端上下文。"""
        import asyncio
        import time

        # 制造一个「看似 3 分钟前活跃」的上下文。
        ctx = self.store._ctx("u1")
        with ctx._lock:
            ctx.frame_at = time.monotonic() - 180.0
            ctx.snapshot_at = time.monotonic() - 180.0
        removed = self.store.sweep()
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("u1", self.store._contexts)

    def test_privacy_reload_updates_rules(self):
        """Phase 6：配置热更新后隐私规则立即生效。"""
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig

        self.store.configure(ScreenConfig(enabled=True), enabled=True)
        blocked, reason = self.store._privacy.check(
            __import__(
                "src.open_llm_vtuber.screen_awareness.models", fromlist=["ScreenWindowInfo"]
            ).ScreenWindowInfo(title="工资条", app="excel", pid=1)
        )
        self.assertFalse(blocked)  # 默认无用户黑名单

        self.store.configure(
            ScreenConfig(enabled=True, blocked_title_keywords=["工资条"]),
            enabled=True,
        )
        blocked2, reason2 = self.store._privacy.check(
            __import__(
                "src.open_llm_vtuber.screen_awareness.models", fromlist=["ScreenWindowInfo"]
            ).ScreenWindowInfo(title="6月工资条.xlsx", app="excel", pid=1)
        )
        self.assertTrue(blocked2)
        self.assertEqual(reason2, "user_blocklist_title")

    def test_concurrent_ingest_keeps_latest_frame(self):
        """慢模型期间保留最新帧，旧任务完成后继续分析最新画面。"""
        import asyncio
        from unittest.mock import patch
        from src.open_llm_vtuber.screen_awareness import metrics as metrics_mod
        from src.open_llm_vtuber.screen_awareness.models import ScreenFrame, ScreenSnapshot

        analyzed_hashes = []

        async def slow_analyze(frame, api_key=""):
            analyzed_hashes.append(frame.image_hash)
            await asyncio.sleep(0.2)
            return ScreenSnapshot(
                scene="coding", summary=frame.image_hash, confidence=0.9
            )

        metrics_mod.get_metrics().frames_dropped = 0
        metrics_mod.get_metrics().frames_captured = 0

        async def run():
            with patch.object(self.store.analyzer, "analyze", slow_analyze):
                f1 = ScreenFrame.model_validate(make_frame(image_hash="h-single-1"))
                f2 = ScreenFrame.model_validate(make_frame(image_hash="h-single-2"))
                # 并发发起两帧：第二帧保留，第一帧完成后继续分析第二帧。
                t1 = asyncio.create_task(self.store.ingest("u1", f1))
                t2 = asyncio.create_task(self.store.ingest("u1", f2))
                await asyncio.gather(t1, t2)

        asyncio.run(run())
        m = metrics_mod.get_metrics()
        self.assertEqual(m.frames_captured, 2)
        self.assertGreaterEqual(m.frames_dropped, 1)
        self.assertEqual(analyzed_hashes, ["h-single-1", "h-single-2"])
        self.assertEqual(self.store.status("u1").last_summary, "h-single-2")

    def test_clear_invalidates_inflight_result(self):
        """清除期间完成的旧视觉请求不能把摘要写回内存。"""
        import asyncio
        from unittest.mock import patch
        from src.open_llm_vtuber.screen_awareness.models import ScreenFrame, ScreenSnapshot

        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_analyze(_frame, api_key=""):
            started.set()
            await release.wait()
            return ScreenSnapshot(scene="coding", summary="旧摘要", confidence=0.9)

        async def run():
            with patch.object(self.store.analyzer, "analyze", blocked_analyze):
                task = asyncio.create_task(
                    self.store.ingest(
                        "u1", ScreenFrame.model_validate(make_frame(image_hash="old"))
                    )
                )
                await started.wait()
                self.store.clear("u1")
                release.set()
                await task

        asyncio.run(run())
        self.assertIsNone(self.store.latest_snapshot("u1"))
        self.assertEqual(self.store.status("u1").last_summary, "")


class TestPolicy(unittest.TestCase):
    def _decide(self, **snap_kw):
        from src.open_llm_vtuber.screen_awareness.models import PolicyDecision, ScreenConfig, ScreenSnapshot
        from src.open_llm_vtuber.screen_awareness.policy import ProactivePolicy

        policy = ProactivePolicy(ScreenConfig())
        return policy.decide(ScreenSnapshot(**snap_kw))

    def test_coding_issue_interrupt(self):
        d = self._decide(
            scene="coding", worth_interrupting=True, confidence=0.9, possible_topic="报错"
        )
        self.assertEqual(d.kind, "interrupt")
        self.assertTrue(d.hint)

    def test_video_silence(self):
        d = self._decide(scene="video", confidence=0.95)
        self.assertEqual(d.kind, "silence")

    def test_sensitive_silence(self):
        d = self._decide(scene="chat", sensitive=True, confidence=0.9)
        self.assertEqual(d.kind, "silence")
        self.assertEqual(d.reason, "sensitive")

    def test_low_confidence_silence(self):
        d = self._decide(scene="chat", confidence=0.1)
        self.assertEqual(d.kind, "silence")

    def test_chat_light_chat(self):
        d = self._decide(scene="chat", confidence=0.85, possible_topic="旅行")
        self.assertEqual(d.kind, "light_chat")

    def test_user_busy_silence(self):
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenSnapshot
        from src.open_llm_vtuber.screen_awareness.policy import ProactivePolicy

        policy = ProactivePolicy(ScreenConfig())
        d = policy.decide(
            ScreenSnapshot(scene="chat", confidence=0.9), user_busy=True
        )
        self.assertEqual(d.kind, "silence")

    def test_quiet_mode_silence(self):
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenSnapshot
        from src.open_llm_vtuber.screen_awareness.policy import ProactivePolicy

        policy = ProactivePolicy(ScreenConfig())
        d = policy.decide(ScreenSnapshot(scene="chat", confidence=0.9), quiet_mode=True)
        self.assertEqual(d.kind, "silence")

    def test_game_light_chat_default(self):
        """2026-08-11：游戏场景默认允许主动轻聊（桌宠陪伴场景）。"""
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenSnapshot
        from src.open_llm_vtuber.screen_awareness.policy import ProactivePolicy

        policy = ProactivePolicy(ScreenConfig())
        d = policy.decide(ScreenSnapshot(scene="game", confidence=0.9, possible_topic="原神"))
        self.assertEqual(d.kind, "light_chat")
        self.assertEqual(d.reason, "scene_game")
        self.assertTrue(d.hint)
        self.assertIn("原神", d.hint)  # 话题注入 hint

    def test_game_silence_when_disabled(self):
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenSnapshot
        from src.open_llm_vtuber.screen_awareness.policy import ProactivePolicy

        policy = ProactivePolicy(ScreenConfig(proactive_allow_game=False))
        d = policy.decide(ScreenSnapshot(scene="game", confidence=0.9))
        self.assertEqual(d.kind, "silence")
        self.assertEqual(d.reason, "immersive_game")


class TestContextFusion(unittest.TestCase):
    """Phase 3：聊天上下文融合（摘要进 LLM 不进历史；图像仅明确要求时附带）。"""

    def setUp(self):
        from src.open_llm_vtuber.screen_awareness.service import get_store
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig, ScreenSnapshot, ScreenWindowInfo

        self.store = get_store()
        self.store.clear_all()
        self.store.configure(ScreenConfig(enabled=True, summary_ttl_sec=60.0), enabled=True)
        self.store.set_client_enabled("u1", True)
        # 手动放一个有效快照 + 帧。
        import time as _t

        ctx = self.store._ctx("u1")
        with ctx._lock:
            ctx.snapshot = ScreenSnapshot(
                scene="coding", summary="用户正在 VS Code 中查看 TypeScript 报错",
                possible_topic="类型错误", confidence=0.9,
            )
            ctx.snapshot_at = _t.monotonic()
            ctx.window = ScreenWindowInfo(title="VS Code - main.ts", app="Code", pid=123)
            from src.open_llm_vtuber.screen_awareness.models import ScreenFrame
            ctx.frame = ScreenFrame.model_validate(make_frame(image_hash="hctx"))
            ctx.frame_at = _t.monotonic()

    def tearDown(self):
        self.store.clear_all()

    def _attach(self, text, images=None, proactive=False):
        from src.open_llm_vtuber.conversations.single_conversation import _attach_screen_context

        out, imgs = _attach_screen_context("u1", text, images, proactive)
        return out, imgs

    def test_summary_injected_into_llm_input(self):
        out, _imgs = self._attach("帮我看看这个报错")
        self.assertIn("[屏幕上下文]", out)
        self.assertIn("VS Code", out)

    def test_screen_image_attached_on_explicit_request(self):
        """修复（2026-08-11）：默认不附图像（无视觉 LLM 会崩溃），只注入摘要；
        开启 attach_screen_image_to_llm 后附带 dict 图像（create_batch_input 期望格式）。"""
        out, imgs = self._attach("你看我这里哪里写错了")
        self.assertIn("[屏幕上下文]", out)  # 摘要仍注入
        self.assertIsNone(imgs)  # 默认不附图像（DeepSeek 无视觉）

        self.store.configure(
            __import__(
                "src.open_llm_vtuber.screen_awareness.models", fromlist=["ScreenConfig"]
            ).ScreenConfig(
                enabled=True,
                summary_ttl_sec=60.0,
                attach_screen_image_to_llm=True,
                chat_model_supports_vision=False,
            ),
            enabled=True,
        )
        _out_no_cap, imgs_no_cap = self._attach("你看我这里哪里写错了")
        self.assertIsNone(imgs_no_cap)

        self.store.configure(
            __import__(
                "src.open_llm_vtuber.screen_awareness.models", fromlist=["ScreenConfig"]
            ).ScreenConfig(
                enabled=True,
                summary_ttl_sec=60.0,
                attach_screen_image_to_llm=True,
                chat_model_supports_vision=True,
            ),
            enabled=True,
        )
        out2, imgs2 = self._attach("你看我这里哪里写错了")
        self.assertIsNotNone(imgs2)
        self.assertEqual(imgs2[0]["source"], "screen")
        self.assertIn("data:image", imgs2[0]["data"])
        self.assertEqual(imgs2[0]["mime_type"], "image/jpeg")

    def test_no_image_without_keyword(self):
        out, imgs = self._attach("今天天气怎么样")
        self.assertIn("[屏幕上下文]", out)  # 摘要仍注入
        self.assertIsNone(imgs)

    def test_proactive_skips_summary(self):
        out, _imgs = self._attach("随便说点什么", proactive=True)
        self.assertNotIn("[屏幕上下文]", out)


class TestMetrics(unittest.TestCase):
    def test_snapshot_shape(self):
        from src.open_llm_vtuber.screen_awareness.metrics import get_metrics

        m = get_metrics()
        m.inc("frames_captured")
        m.observe("capture", 12.5)
        snap = m.snapshot()
        self.assertIn("dedupe_ratio", snap)
        self.assertIn("capture_p50_ms", snap)
        self.assertGreaterEqual(snap["frames_captured"], 1)


class TestProactiveSnapshotDedup(unittest.TestCase):
    """Phase 2（pet-ptt-workflow）：快照版本去重——静态画面不重复打扰。

    验证 decide_proactive_for 只允许「上次主动对话之后产生的新快照」通过：
    - 首次主动（无 last_proactive_snapshot_id）→ 允许；
    - 之后无新快照 → no_new_snapshot 拦截；
    - 有新快照（ingest 分析成功 seq+1）→ 再次允许，然后回到静态拦截。
    """

    def setUp(self):
        from src.open_llm_vtuber.screen_awareness.models import ScreenConfig
        from src.open_llm_vtuber.screen_awareness.service import get_store

        self.store = get_store()
        self.store.clear_all()
        self.store.configure(
            ScreenConfig(
                enabled=True,
                poll_interval_sec=3.0,
                proactive_cooldown_sec=0.001,
                proactive_min_confidence=0.5,
            ),
            enabled=True,
        )
        self.store.set_client_enabled("u1", True)
        self._reset_cooldown(clear_snapshot_mark=True)

    def tearDown(self):
        self.store.clear_all()

    def _reset_cooldown(self, clear_snapshot_mark: bool = False):
        """绕开 180s 冷却（第二道闸门），聚焦快照版本去重检查。

        只清冷却时间戳；版本标记（last_proactive_snapshot_id）默认保留——
        那是本测试要验证的去重依据。
        """
        ctx = self.store._ctx("u1")
        with ctx._lock:
            ctx.last_proactive_at = 0.0
            if clear_snapshot_mark:
                ctx.last_proactive_snapshot_id = None

    def _ingest(self, image_hash="h-1"):
        import asyncio
        from unittest.mock import patch

        from src.open_llm_vtuber.screen_awareness.models import ScreenFrame, ScreenSnapshot

        async def analyze(_frame, api_key=""):
            return ScreenSnapshot(
                scene="coding",
                summary="测试画面",
                confidence=0.9,
                worth_interrupting=True,
            )

        with patch.object(self.store.analyzer, "analyze", analyze):
            asyncio.run(
                self.store.ingest(
                    "u1",
                    ScreenFrame.model_validate(make_frame(image_hash=image_hash)),
                    api_key="",
                )
            )

    def test_static_snapshot_not_retriggered(self):
        from src.open_llm_vtuber.screen_awareness.policy import decide_proactive_for

        self._ingest("h-1")
        d1 = decide_proactive_for("u1")
        self.assertNotEqual(d1.kind, "silence", "首次主动（新快照）应允许")

        self._reset_cooldown()  # 只留版本去重这道闸门
        d2 = decide_proactive_for("u1")
        self.assertEqual(d2.kind, "silence", "静态画面不得重复主动")
        self.assertEqual(d2.reason, "no_new_snapshot")

    def test_new_snapshot_allows_next_proactive(self):
        from src.open_llm_vtuber.screen_awareness.policy import decide_proactive_for

        self._ingest("h-1")
        d1 = decide_proactive_for("u1")
        self.assertNotEqual(d1.kind, "silence")

        self._reset_cooldown()
        d2 = decide_proactive_for("u1")
        self.assertEqual(d2.kind, "silence")
        self.assertEqual(d2.reason, "no_new_snapshot")

        # 新快照（内容变化）→ 再次允许。
        self._ingest("h-2")
        self._reset_cooldown()
        d3 = decide_proactive_for("u1")
        self.assertNotEqual(d3.kind, "silence")

        # 又回到静态：需要再次新快照。
        self._reset_cooldown()
        d4 = decide_proactive_for("u1")
        self.assertEqual(d4.kind, "silence")
        self.assertEqual(d4.reason, "no_new_snapshot")

    def test_has_new_snapshot_gate_direct(self):
        # 未启用/无快照 → False
        self.assertFalse(self.store.has_new_snapshot_since_last_proactive("u1"))
        self._ingest("h-3")
        self.assertTrue(self.store.has_new_snapshot_since_last_proactive("u1"))
        self.store.mark_proactive("u1")
        # 主动消费后，同版本不再算新
        self.assertFalse(self.store.has_new_snapshot_since_last_proactive("u1"))
        # 新版本恢复
        self._ingest("h-4")
        self.assertTrue(self.store.has_new_snapshot_since_last_proactive("u1"))

    def test_ingest_increments_snapshot_seq(self):
        self._ingest("h-seq-1")
        ctx = self.store._ctx("u1")
        with ctx._lock:
            self.assertGreaterEqual(ctx.snapshot_seq, 1)
        self._ingest("h-seq-2")
        with ctx._lock:
            self.assertGreaterEqual(ctx.snapshot_seq, 2)


if __name__ == "__main__":
    unittest.main()
