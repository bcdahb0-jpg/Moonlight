"""P5.1 收敛测试：附件降级链路 / 意图事件契约 / bridge 桥接 fail-soft。

全部离线可跑：PDF 用 pypdf 内存生成真实样例；bridge 无连接时返回 False
（fail-soft，不依赖运行中的 WS）；意图事件用 contracts 模型序列化校验。
"""

import asyncio
import io
import unittest

from src.open_llm_vtuber.attachment_route import _extract_pdf, init_attachment_route
from src.open_llm_vtuber.contracts import IntentEventMessage
from src.open_llm_vtuber.bridge import feed_as_user_input, speak_line


def _make_pdf_bytes(text: str) -> bytes:
    """用 pypdf 的内存 writer 生成一页真实 PDF。"""
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class TestAttachment(unittest.TestCase):
    def test_route_registered(self):
        router = init_attachment_route()
        paths = [getattr(r, "path", None) for r in router.routes]
        self.assertIn("/api/conversation/attachments", paths)

    def test_pdf_extract_failsoft(self):
        # 假 PDF → 明确报错文案（不抛异常）
        out = asyncio.run(_extract_pdf(b"%PDF-1.4 not real", "t.pdf"))
        self.assertIn("解析失败", out)
        self.assertIn("t.pdf", out)

    def test_pdf_empty_pages_hint(self):
        # 真实 PDF 但无文本（空白页）→ 扫描件提示
        out = asyncio.run(_extract_pdf(_make_pdf_bytes(""), "blank.pdf"))
        self.assertIn("未提取到文本", out)


class TestIntentEvent(unittest.TestCase):
    def test_contract_serialize(self):
        msg = IntentEventMessage(
            type="intent-event", intent="silence", emotion="anger", source="rule", text="别烦我"
        )
        data = msg.model_dump()
        self.assertEqual(data["type"], "intent-event")
        self.assertEqual(data["intent"], "silence")
        self.assertEqual(data["text"], "别烦我")

    def test_contract_chat_default(self):
        msg = IntentEventMessage(type="intent-event", intent="chat", emotion="joy", source="llm")
        data = msg.model_dump()
        self.assertIsNone(data["text"])


class TestBridge(unittest.TestCase):
    def test_no_connection_failsoft(self):
        # 无活跃 WS 连接（测试环境）→ 返回 False，不抛异常
        self.assertFalse(asyncio.run(feed_as_user_input("测试弹幕", source="danmaku")))
        self.assertFalse(asyncio.run(speak_line("太厉害了！", source="cheer")))

    def test_empty_text_failsoft(self):
        self.assertFalse(asyncio.run(feed_as_user_input("   ")))


if __name__ == "__main__":
    unittest.main()
