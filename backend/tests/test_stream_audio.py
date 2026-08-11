"""viseme（音素级口型）与音频 payload 契约测试。

覆盖 docs/live2d-lipsync-plan.md Phase 2：
- 五元音 F1/F2 共振峰分类（合成音频）
- 概率归一化 / 静音兜底
- AudioMessage 契约含 visemes 字段
"""

import unittest

import numpy as np
from pydub import AudioSegment

from src.open_llm_vtuber.contracts import AudioMessage
from src.open_llm_vtuber.utils.stream_audio import prepare_audio_payload
from src.open_llm_vtuber.utils.viseme import compute_visemes

SR = 24000
VOWEL_INDEX = {"a": 0, "i": 1, "u": 2, "e": 3, "o": 4}


def _synth_vowel(freqs: list[float], dur: float = 1.0) -> AudioSegment:
    """合成双共振峰元音音频（16bit mono 24kHz）。"""
    t = np.arange(int(SR * dur)) / SR
    sig = np.zeros_like(t)
    for f in freqs:
        sig += 0.5 * np.sin(2 * np.pi * f * t)
    pcm = (sig * 32767).astype(np.int16)
    return AudioSegment(pcm.tobytes(), frame_rate=SR, sample_width=2, channels=1)


class TestVisemeAlgorithm(unittest.TestCase):
    def test_synthetic_vowels_classify_correctly(self):
        """五元音合成音频，viseme 的 argmax 应落在对应元音上。"""
        cases = {
            "a": [800.0, 1200.0],
            "i": [300.0, 2300.0],
            "u": [350.0, 800.0],
            "e": [500.0, 1800.0],
            "o": [450.0, 900.0],
        }
        for vowel, freqs in cases.items():
            with self.subTest(vowel=vowel):
                vs = compute_visemes(_synth_vowel(freqs), 20)
                self.assertTrue(len(vs) > 0, "应产出 viseme slice")
                mid = vs[len(vs) // 2]
                got = max(range(5), key=lambda i: mid[i])
                self.assertEqual(got, VOWEL_INDEX[vowel], f"合成 {vowel} 元音被判错")

    def test_probabilities_sum_to_one(self):
        """每个 slice 的概率和应为 1（浮点误差允许 1e-6）。"""
        vs = compute_visemes(_synth_vowel([800.0, 1200.0]), 20)
        for i, v in enumerate(vs):
            with self.subTest(slice=i):
                self.assertAlmostEqual(sum(v), 1.0, places=6)

    def test_silence_returns_uniform(self):
        """纯静音（能量全零）应返回均匀分布而非崩溃。"""
        silence = AudioSegment(
            np.zeros(SR, dtype=np.int16).tobytes(),
            frame_rate=SR,
            sample_width=2,
            channels=1,
        )
        vs = compute_visemes(silence, 20)
        self.assertGreater(len(vs), 0)
        self.assertAlmostEqual(sum(vs[0]), 1.0, places=6)

    def test_empty_audio_returns_empty(self):
        empty = AudioSegment(b"", frame_rate=SR, sample_width=2, channels=1)
        self.assertEqual(compute_visemes(empty, 20), [])


class TestAudioPayloadContract(unittest.TestCase):
    def test_audio_message_schema_has_visemes(self):
        """出站 AudioMessage 契约包含 visemes 字段（默认空列表）。"""
        msg = AudioMessage(type="audio")
        self.assertEqual(msg.visemes, [])
        self.assertIn("visemes", msg.model_dump(exclude_none=True))

    def test_silent_payload_visemes_empty(self):
        """无音频的 payload 应携带空 visemes。"""
        payload = prepare_audio_payload(None)
        self.assertEqual(payload["visemes"], [])


if __name__ == "__main__":
    unittest.main()
