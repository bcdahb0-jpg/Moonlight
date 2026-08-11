"""LLM 翻译引擎测试：输出侧净化（提示语垃圾剥离）+ 空结果回退。

背景：DeepSeek 翻译偶尔不遵守「只输出译文」规则，在译文前后追加
「请发送需要翻译的中文台词内容」类引导语（实测 '😊' -> '請傳送要翻譯的
中文台詞...'；正常长文本译文后追加「请发送需要翻译的中文台词内容」）。
sanitize_translation 负责剥离这类垃圾；剥离后为空 -> 回退原文。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_llm_translate.py -q
"""
import unittest
from unittest import mock

from src.open_llm_vtuber.translate.llm_translate import (
    LLMTranslate,
    sanitize_translation,
)


class TestSanitizeTranslation(unittest.TestCase):
    """净化纯函数：正常译文不动，提示语垃圾剥离，纯垃圾返回空。"""

    def test_keeps_clean_translation(self):
        cases = [
            "Done! I've generated the file for you.",
            "已经把文件生成好了，直接打开就能看。",
            "3.14159 和 42 都算实质内容。",
        ]
        for text in cases:
            self.assertEqual(sanitize_translation(text), text, text)

    def test_strips_trailing_prompt_noise_cn(self):
        # 截图实证：正常英文译文后追加「请发送需要翻译的中文台词内容。」
        out = sanitize_translation(
            "Done! I've generated the file for you~ 请发送需要翻译的中文台词内容。"
        )
        self.assertEqual(out, "Done! I've generated the file for you~")

    def test_strips_leading_prompt_noise_traditional(self):
        # v6.1 实测：'😊' -> 「請傳送要翻譯的中文台詞...」整段都是提示语
        out = sanitize_translation("請傳送要翻譯的中文台詞...")
        self.assertEqual(out, "")

    def test_strips_prompt_noise_variants(self):
        cases = [
            "Hello. 请输入需要翻译的内容。",
            "Hello. 请把需要翻译的中文内容发给我。",
            "Hello. 请提供要翻译的文本。",
            "Hello. 需要翻译的中文台词内容请在下方发送。",
            "Hello. Please send the text you want to translate.",
            "Hello. Please provide the Chinese dialogue for translation.",
        ]
        for text in cases:
            self.assertEqual(
                sanitize_translation(text), "Hello.", f"input: {text!r}"
            )

    def test_strips_prefix_wrap(self):
        self.assertEqual(sanitize_translation("翻译：你好世界"), "你好世界")
        self.assertEqual(sanitize_translation("譯文: Hello world"), "Hello world")
        self.assertEqual(sanitize_translation("Translation: Hi"), "Hi")

    def test_strips_quotes(self):
        self.assertEqual(sanitize_translation('"你好世界"'), "你好世界")
        self.assertEqual(sanitize_translation("「你好世界」"), "你好世界")

    def test_empty_stays_empty(self):
        self.assertEqual(sanitize_translation(""), "")
        self.assertEqual(sanitize_translation("   "), "")


class _FakeResp:
    def __init__(self, content: str):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class TestLLMTranslateFailSoft(unittest.TestCase):
    """引擎级：净化后为空/网络异常 -> 回退原文（fail-soft）。"""

    def _make(self):
        return LLMTranslate(
            api_endpoint="http://fake/v1/chat/completions",
            model="fake-model",
            target_lang="英語",
        )

    @mock.patch("httpx.post")
    def test_prompt_noise_only_falls_back_to_original(self, mock_post):
        mock_post.return_value = _FakeResp("請傳送要翻譯的中文台詞...")
        engine = self._make()
        self.assertEqual(engine.translate("😊"), "😊")

    @mock.patch("httpx.post")
    def test_clean_translation_returned(self, mock_post):
        mock_post.return_value = _FakeResp("Hello there!")
        engine = self._make()
        self.assertEqual(engine.translate("你好呀"), "Hello there!")

    @mock.patch("httpx.post")
    def test_noise_after_translation_stripped(self, mock_post):
        mock_post.return_value = _FakeResp(
            "Done! I've generated it~ 请发送需要翻译的中文台词内容。"
        )
        engine = self._make()
        self.assertEqual(engine.translate("搞定啦！"), "Done! I've generated it~")

    @mock.patch("httpx.post")
    def test_network_error_falls_back(self, mock_post):
        mock_post.side_effect = Exception("boom")
        engine = self._make()
        self.assertEqual(engine.translate("你好"), "你好")


if __name__ == "__main__":
    unittest.main()
