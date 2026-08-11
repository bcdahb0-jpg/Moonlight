"""音素级口型数据（visemes）计算 —— F1/F2 共振峰 + 预置元音模板。

为什么不用 librosa（pymouth 的方案是 MFCC + DTW + temperature softmax）：
librosa 是重依赖；且 20ms slice 的 MFCC 帧太少，DTW 模板（9 帧）不稳定。
本实现用**共振峰**替代：元音的本质就是 F1/F2 位置，标准语音学值即可做模板，
完全自洽、零新依赖（numpy 已有），每 slice 计算量毫秒级。

算法：
1. AudioSegment → int16 样本 → float [-1, 1]（取左声道）
2. 60ms 分析窗口、20ms 步进；窗口内分帧（512 FFT / 160 hop，~21ms/帧）
3. 每帧：能量门限过滤静音 → rFFT → 功率谱 → F1（100-1000Hz 峰）/ F2（1000-3200Hz 峰）
4. 帧级 (F1,F2) 与五元音模板（普通话/日语标准值）在 log 域的欧氏距离
   → 温度 softmax（temperature=10，压低置信度让口型更平滑，同 pymouth）
5. slice 内多帧概率平均 → 每 slice 输出 [a,i,u,e,o] 概率

模板（F1, F2，单位 Hz，普通话/日语元音标准值）：
  /a/ 开口低舌位：F1≈800  F2≈1200
  /i/ 闭口前舌位：F1≈300  F2≈2300
  /u/ 闭口后舌位：F1≈350  F2≈800
  /e/ 半闭前舌位：F1≈500  F2≈1800
  /o/ 半闭后舌位：F1≈450  F2≈900
"""

from __future__ import annotations

import numpy as np
from loguru import logger
from pydub import AudioSegment

VOWEL_NAMES = ("a", "i", "u", "e", "o")
VOWEL_TEMPLATES: tuple[tuple[float, float], ...] = (
    (800.0, 1200.0),  # a
    (300.0, 2300.0),  # i
    (350.0, 800.0),   # u
    (500.0, 1800.0),  # e
    (450.0, 900.0),   # o
)

_FRAME_LEN = 512      # ~21ms @ 24kHz
_FRAME_HOP = 160      # ~6.7ms
_WINDOW_MS = 60       # 共振峰分析窗口（切片太短会抖，取上下文）
_MIN_ENERGY = 0.005   # 帧 RMS 门限：低于视为静音帧，不参与元音概率平均
# softmax 温度：温度越小分布越尖。pymouth 用 10 是因为 DTW normalizedDistance
# 尺度接近 1；本实现的 log 域欧氏距离差值约 0.6-1.2，T=10 会稀释成均匀分布。
# T=0.8 时主元音概率 ~0.4-0.5，区分度足够，且前端 driver 的 attack/release
# 平滑会柔化口型切换，不会生硬。
_TEMPERATURE = 0.8

_FFT_WINDOW = np.hanning(_FRAME_LEN)


def compute_visemes(audio: AudioSegment, slice_ms: int = 20) -> list[list[float]]:
    """对整段音频逐 slice 输出 [a,i,u,e,o] 概率向量。

    Args:
        audio: pydub AudioSegment（任意格式/采样率，内部统一处理）。
        slice_ms: 输出粒度（应与 volumes 的 slice_length 一致，默认 20ms）。

    Returns:
        list[list[float]]: 每 slice 一个 5 元素概率向量（和为 1）。
        空音频/异常时返回 []；静音 slice 返回均匀分布（前端以音量驱动开合，
        元音概率只在有声时才有形状意义）。
    """
    try:
        samples = _to_float_samples(audio)
        sr = audio.frame_rate
        if sr <= 0 or samples.size == 0:
            return []
    except Exception as e:  # pragma: no cover - 保底不炸音频链路
        logger.debug(f"viseme: samples extraction failed: {type(e).__name__}")
        return []

    slice_n = max(1, int(round(sr * slice_ms / 1000)))
    win_n = int(round(sr * _WINDOW_MS / 1000))
    uniform = [1.0 / len(VOWEL_NAMES)] * len(VOWEL_NAMES)

    visemes: list[list[float]] = []
    pos = 0
    total = samples.size
    while pos + slice_n <= total:
        window = samples[pos : min(pos + win_n, total)]
        if window.size < _FRAME_LEN:
            # 尾部不足一帧：按静音处理（前端此时音量也已归零）
            visemes.append(list(uniform))
            break
        probs = _window_probability(window, sr)
        visemes.append(probs if probs is not None else list(uniform))
        pos += slice_n
    return visemes


def _to_float_samples(audio: AudioSegment) -> np.ndarray:
    """AudioSegment → float32 单声道样本 [-1, 1]。"""
    raw = np.frombuffer(audio.get_array_of_samples(), dtype=np.int16)
    if audio.channels > 1:
        raw = raw[:: audio.channels]  # 取左声道
    return raw.astype(np.float32) / 32768.0


def _window_probability(window: np.ndarray, sr: int) -> list[float] | None:
    """对一段窗口样本分帧，逐帧估计 F1/F2，聚合出元音概率分布。"""
    frames = _split_frames(window)
    frame_probs: list[np.ndarray] = []
    for frame in frames:
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms < _MIN_ENERGY:
            continue  # 静音帧跳过，避免噪声共振峰污染
        formants = _estimate_formants(frame, sr)
        if formants is None:
            continue
        frame_probs.append(_formant_probability(*formants))
    if not frame_probs:
        return None
    avg = np.mean(np.stack(frame_probs), axis=0)
    # 数值兜底：保证概率和为 1
    total = float(avg.sum())
    if total <= 0:
        return None
    return (avg / total).tolist()


def _split_frames(window: np.ndarray) -> list[np.ndarray]:
    """512 帧长 / 160 hop 滑窗分帧；末尾不足补零。"""
    frames: list[np.ndarray] = []
    i = 0
    while i + _FRAME_LEN <= window.size:
        frames.append(window[i : i + _FRAME_LEN] * _FFT_WINDOW)
        i += _FRAME_HOP
    tail = window[i:]
    if tail.size > 0:
        padded = np.zeros(_FRAME_LEN, dtype=window.dtype)
        padded[: tail.size] = tail
        frames.append(padded * _FFT_WINDOW)
    return frames


def _estimate_formants(frame: np.ndarray, sr: int) -> tuple[float, float] | None:
    """rFFT 功率谱估计 F1（第一共振峰）与 F2（第二共振峰）。

    真实语音里 F1 是最低频的显著峰（100-1000Hz），F2 是其上方的显著峰
    （1000-3200Hz）。用「最低频显著峰」而不是全局 argmax，避免等幅/谐波
    分量抢走 F1（合成测试中 350+800Hz 等幅时 argmax 会误取 800）。
    """
    spec = np.abs(np.fft.rfft(frame))
    freqs = np.fft.rfftfreq(_FRAME_LEN, 1.0 / sr)

    band = (freqs >= 100.0) & (freqs <= 3200.0)
    if not np.any(band):
        return None
    f = freqs[band]
    p = spec[band]
    peak = float(p.max())
    if peak <= 0:
        return None
    # 显著峰阈值：不低于最强峰的一半（抑制噪声伪峰）
    strong = p >= peak * 0.5

    # F1：100-1000Hz 内最低频显著峰
    f1_band = (f >= 100.0) & (f <= 1000.0)
    cand1 = f[f1_band & strong]
    if cand1.size:
        f1 = float(cand1[0])
    elif np.any(f1_band):
        f1 = float(f[f1_band][np.argmax(p[f1_band])])
    else:
        return None

    # F2：1000-3200Hz 内最低频显著峰，避开 F1 ± 300Hz 邻域（防 F1 谐波）
    f2_band = (f >= 1000.0) & (f <= 3200.0) & (np.abs(f - f1) > 300.0)
    cand2 = f[f2_band & strong]
    if cand2.size:
        f2 = float(cand2[0])
    else:
        # 退路：避开 F1 邻域后的最强峰（含 F2 < 1000Hz 的后元音 /u/ /o/；
        # 必须排除 F1 的 FFT 泄漏峰，否则会把 F1 旁边的 bin 误当 F2）
        alt = (f >= 100.0) & (np.abs(f - f1) > 300.0)
        if not np.any(alt):
            return None
        f2 = float(f[alt][np.argmax(p[alt])])
    return f1, f2


def _formant_probability(f1: float, f2: float) -> np.ndarray:
    """(F1,F2) 与五元音模板在 log 域的欧氏距离 → temperature softmax。"""
    log_f1, log_f2 = np.log(f1), np.log(f2)
    dists = np.array(
        [
            np.sqrt((log_f1 - np.log(t1)) ** 2 + (log_f2 - np.log(t2)) ** 2)
            for t1, t2 in VOWEL_TEMPLATES
        ]
    )
    r = -dists / _TEMPERATURE
    r = r - float(np.max(r))  # 数值稳定
    exp = np.exp(r)
    total = float(exp.sum())
    return exp / total if total > 0 else np.full(len(VOWEL_NAMES), 1.0 / len(VOWEL_NAMES))
