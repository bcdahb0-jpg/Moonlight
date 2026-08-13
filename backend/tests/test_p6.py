"""P6 进阶测试：本地 zip 安装 / 遮罩 AI 前景 / QQ 连接器 / 音频附件。

全部离线可跑：zip 安装用临时目录（不碰真实 plugins/）；遮罩用 Pillow
合成图；QQ 客户端只测状态/文本提取（不真实连接）；音频转写测 fail-soft。
"""

import asyncio
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.open_llm_vtuber.attachment_route import _transcribe_audio
from src.open_llm_vtuber.occlusion_route import (
    _extract_with_pillow,
    _GRID,
    init_occlusion_route,
)
from src.open_llm_vtuber.plugin_route import (
    _install_zip_bytes,
    init_plugin_route,
)
from src.open_llm_vtuber.social.qq_client import _extract_text, get_qq_client


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_png_bytes(size: int = 64, fill: tuple = (10, 10, 10)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (size, size), (255, 255, 255))
    if fill:
        from PIL import ImageDraw

        ImageDraw.Draw(img).rectangle([size // 4, size // 4, size * 3 // 4, size * 3 // 4], fill=fill)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestInstallZip(unittest.TestCase):
    def test_route_registered(self):
        paths = [getattr(r, "path", None) for r in init_plugin_route().routes]
        self.assertIn("/api/plugin/install-zip", paths)
        self.assertIn("/api/plugin/config", paths)

    def test_zip_missing_plugin_json(self):
        out = _install_zip_bytes(_zip_with({"plugin/main.py": "x"}), "t.zip")
        self.assertFalse(out["ok"])
        self.assertIn("plugin.json", out["error"])

    def test_zip_valid_installs_to_tmp(self):
        # 用临时 PLUGINS_ROOT 验证原子落盘（不碰真实 plugins/）
        from src.open_llm_vtuber.plugin import registry

        tmp = Path(tempfile.mkdtemp())
        old_root = registry.PLUGINS_ROOT
        try:
            registry.PLUGINS_ROOT = tmp
            zipped = _zip_with(
                {
                    "hello/plugin.json": b'{"name": "hello", "version": "0.1.0", "hooks": ["on_message"]}',
                    "hello/main.py": b"def on_message(m): return None",
                }
            )
            out = _install_zip_bytes(zipped, "hello.zip")
            self.assertTrue(out["ok"])
            self.assertEqual(out["plugin_id"], "community/hello")
            self.assertTrue((tmp / "community" / "hello" / "plugin.json").is_file())
        finally:
            registry.PLUGINS_ROOT = old_root
            shutil.rmtree(tmp, ignore_errors=True)

    def test_zip_zip_slip_rejected(self):
        out = _install_zip_bytes(
            _zip_with({"../../evil/plugin.json": b'{"name":"evil"}'}), "evil.zip"
        )
        self.assertFalse(out["ok"])


class TestOcclusion(unittest.TestCase):
    def test_route_registered(self):
        paths = [getattr(r, "path", None) for r in init_occlusion_route().routes]
        self.assertIn("/api/occlusion/extract", paths)

    def test_pillow_solid_none(self):
        from PIL import Image

        img = Image.new("RGB", (64, 64), (255, 255, 255)).convert("RGBA")
        self.assertIsNone(_extract_with_pillow(img))

    def test_pillow_square_outline(self):
        from PIL import Image

        img = Image.new("RGB", (64, 64), (255, 255, 255))
        from PIL import ImageDraw

        ImageDraw.Draw(img).rectangle([16, 16, 48, 48], fill=(10, 10, 10))
        pts = _extract_with_pillow(img.convert("RGBA"))
        self.assertIsNotNone(pts)
        self.assertTrue(3 <= len(pts) <= 96)
        # 坐标范围 0-100
        for p in pts:
            self.assertTrue(0 <= p["x"] <= 100)
            self.assertTrue(0 <= p["y"] <= 100)


class TestQqClient(unittest.TestCase):
    def test_extract_text(self):
        msg = [
            {"type": "text", "data": {"text": "你好"}},
            {"type": "face", "data": {"id": 1}},
            {"type": "text", "data": {"text": "，在吗"}},
        ]
        self.assertEqual(_extract_text(msg), "你好，在吗")
        self.assertEqual(_extract_text("直接字符串"), "直接字符串")

    def test_status_default(self):
        status = get_qq_client().status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["connected"])
        self.assertEqual(status["url"], "ws://127.0.0.1:3001")
        self.assertEqual(status["msg_count"], 0)


class TestAudioAttachment(unittest.TestCase):
    def test_non_wav_hint(self):
        out = asyncio.run(_transcribe_audio(b"ID3xx", "audio/mpeg", "a.mp3"))
        self.assertIn("仅支持 wav", out)

    def test_fake_wav_failsoft(self):
        out = asyncio.run(_transcribe_audio(b"RIFF....WAVEfmt", "audio/wav", "v.wav"))
        self.assertIn("转写失败", out)


if __name__ == "__main__":
    unittest.main()


class TestAuditWiring(unittest.TestCase):
    """P6 审计补缺：插件钩子/唱歌触发/弹幕开关/喝彩开关接线验证。"""

    def test_direct_reply_branch_loads(self):
        # single_conversation 替代回复分支：模块可加载 + 关键依赖可 import
        import src.open_llm_vtuber.conversations.single_conversation as sc

        self.assertTrue(callable(sc.process_single_conversation))
        from src.open_llm_vtuber.plugin.manager import get_plugin_manager
        from src.open_llm_vtuber.singing.sing_core import extract_sing_request

        self.assertTrue(callable(get_plugin_manager))
        self.assertEqual(extract_sing_request("唱歌+打上花火"), "打上花火")

    def test_cheer_broadcast_respects_switch(self):
        # cheer_enabled=False → speak_line 不被调用（bridge 不打扰）
        import src.open_llm_vtuber.playmate.route as route

        async def scenario(cheer_enabled: bool) -> bool:
            calls: list[str] = []

            async def fake_speak_line(text, source="cheer"):
                calls.append(text)
                return True

            real_conf = route._playmate_config_from_conf
            real_bridge = None
            import builtins

            try:
                route._playmate_config_from_conf = lambda: {"cheer_enabled": cheer_enabled}
                # patch bridge.speak_line（懒 import，走 sys.modules 不现实——直接 patch 内部 import）
                import importlib
                import src.open_llm_vtuber.bridge as bridge_mod

                orig = bridge_mod.speak_line
                bridge_mod.speak_line = fake_speak_line
                # _broadcast_cheer 内 `from ..bridge import speak_line` 会拿 patched 引用吗？
                # 模块已缓存 → 取到 patched 的 speak_line ✓（同模块对象）
                await route._broadcast_cheer({"cheer": "漂亮！"})
                bridge_mod.speak_line = orig
                route._playmate_config_from_conf = real_conf
                return bool(calls)
            except Exception:
                route._playmate_config_from_conf = real_conf
                raise

        self.assertFalse(asyncio.run(scenario(False)))
        # 开关打开时 bridge 应有调用（无 WS 连接 → speak_line 内部返回 False 但被调用）
        self.assertTrue(asyncio.run(scenario(True)))

    def test_live_default_has_chat_switch(self):
        from src.open_llm_vtuber.live.live_route import DEFAULT_CONFIG, _public_config

        self.assertIn("chat_to_conversation", DEFAULT_CONFIG)
        public = _public_config({**DEFAULT_CONFIG})
        self.assertTrue(public["chat_to_conversation"])
