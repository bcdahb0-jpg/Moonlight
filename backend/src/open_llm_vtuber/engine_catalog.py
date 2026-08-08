"""
Engine catalog — 引擎目录 / 配置状态单一事实源。

为 ASR/TTS 引擎管理（设置面板 + 角色卡）提供：
- 引擎中文名（角色卡下拉不再显示裸英文 id）
- 分类（cloud=云端API / local=本地模型 / local_service=本地服务 / builtin=内置）
- 可配置字段 schema（设置面板自动生成配置表单）
- 配置状态判定（api_key 是否为占位符、本地模型文件是否存在、本地服务地址是否配置）

角色卡只展示 configured=True 的引擎；未配置好的引擎在设置面板里仍可见（可补齐配置）。
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from .config_manager.utils import read_yaml

CONF_PATH = "conf.yaml"

# --------------------------------------------------------------------------- #
# 占位符 / 空值判定（api_key 等敏感字段用）
# --------------------------------------------------------------------------- #
PLACEHOLDER_PATTERNS = (
    "your ",
    "your_",
    "api-key",
    "api_key",
    "xxx",
    "placeholder",
    "something",
    "not-needed",
    "azure-api-key",
    "sk-xxx",
    "<",
    ">",
    "example",
    "change me",
)


def is_placeholder(value: Any) -> bool:
    """True 表示该值仍是默认占位（未真正配置）。"""
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    return any(p in low for p in PLACEHOLDER_PATTERNS)


def _file_exists(value: Any) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    if is_placeholder(s):
        return False
    # conf 里的路径可能是相对 backend 工作目录的
    return os.path.isfile(s) or os.path.isfile(os.path.normpath(s))


# --------------------------------------------------------------------------- #
# 本地服务在线探测（TCP 连通性，带短 TTL 缓存）
# --------------------------------------------------------------------------- #
_PROBE_CACHE: dict[tuple[str, int], tuple[float, bool]] = {}
_PROBE_TTL = 3.0   # 秒：服务状态在缓存期内复用，避免频繁探测
_PROBE_TIMEOUT = 0.5  # 秒：本机端口未监听会立即 RST，超时仅兜底防火墙黑洞


def _service_online(url: Any) -> bool:
    """本地服务引擎：TCP 探测 host:port 是否在线（带 3s 缓存）。"""
    try:
        s = str(url or "").strip()
        parts = urlsplit(s if "://" in s else f"http://{s}")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except Exception:
        return False
    now = time.time()
    cached = _PROBE_CACHE.get((host, port))
    if cached and now - cached[0] < _PROBE_TTL:
        return cached[1]
    ok = False
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT):
            ok = True
    except OSError:
        ok = False
    _PROBE_CACHE[(host, port)] = (now, ok)
    return ok


# --------------------------------------------------------------------------- #
# 字段 schema（设置面板表单自动生成）
# --------------------------------------------------------------------------- #
def _field(key: str, label: str, ftype: str = "text", placeholder: str = "", hint: str = "") -> dict:
    return {"key": key, "label": label, "type": ftype, "placeholder": placeholder, "hint": hint}


# --------------------------------------------------------------------------- #
# 配置读取（读 conf.yaml 的 asr_config / tts_config 子块）
# --------------------------------------------------------------------------- #
def _raw_conf() -> dict:
    data = read_yaml(CONF_PATH) or {}
    return (data.get("character_config", {}) or {}).get("asr_config", {}), \
           (data.get("character_config", {}) or {}).get("tts_config", {})


def _sub_block(block: dict, engine: str) -> dict:
    if not isinstance(block, dict):
        return {}
    sub = block.get(engine, {})
    return sub if isinstance(sub, dict) else {}


# --------------------------------------------------------------------------- #
# 状态判定器
# --------------------------------------------------------------------------- #
def _status_key(block: dict, engine: str, key_name: str = "api_key") -> tuple[bool, str]:
    """云端引擎：api_key 非空且非占位 -> 已配置。"""
    sub = _sub_block(block, engine)
    if is_placeholder(sub.get(key_name)):
        return False, "缺少 API Key（或仍是占位符）"
    return True, "API Key 已配置"


def _status_url(block: dict, engine: str, url_name: str = "api_url") -> tuple[bool, str]:
    """本地服务引擎：服务地址已配置且服务在线 -> 已配置（未在线的地址不再算可用）。"""
    sub = _sub_block(block, engine)
    url = sub.get(url_name)
    if url_name == "base_url" and engine == "voicevox_tts":
        # VOICEVOX 用内置管理（下载/启动），配置态只看本地引擎是否可连
        return _status_voicevox(block, engine)
    if is_placeholder(url):
        return False, "缺少服务地址"
    if not _service_online(url):
        return False, "服务地址已配置，但服务未在线（启动该服务后自动变为可用）"
    return True, "服务在线"


def _status_path(block: dict, engine: str, path_name: str = "model_path") -> tuple[bool, str]:
    """本地模型引擎：模型文件存在 -> 已配置。"""
    sub = _sub_block(block, engine)
    p = sub.get(path_name)
    if not p or is_placeholder(p):
        return False, "模型文件路径未配置或为占位路径"
    if not _file_exists(p):
        return False, f"模型文件不存在：{p}"
    return True, "模型文件就绪"


def _status_always(_block: dict, _engine: str) -> tuple[bool, str]:
    """无条件可用（免费/内置/零配置）。"""
    return True, "无需配置"


def _status_manual_install(_block: dict, _engine: str) -> tuple[bool, str]:
    """依赖未打包进项目、需用户手动 pip install 的引擎 -> 视为未配置。"""
    return False, "需手动安装依赖（pip install）后可用"


def _status_pyttsx3(_block: dict, _engine: str) -> tuple[bool, str]:
    """Windows 内置引擎：可用但不纳入角色卡（音质机械，仅应急）。"""
    return False, "系统内置引擎（角色卡不提供，音质机械）"


def _status_sherpa_asr(block: dict, engine: str) -> tuple[bool, str]:
    """内置 sherpa-onnx ASR：SenseVoice 模型已下载 -> 可用。"""
    sub = _sub_block(block, engine)
    model = sub.get("sense_voice")
    if not model or is_placeholder(model):
        return False, "SenseVoice 模型路径未配置"
    if not _file_exists(model):
        return False, "SenseVoice 模型未下载（首次语音输入会自动下载）"
    return True, "本地模型就绪"


def _status_voicevox(_block: dict, _engine: str) -> tuple[bool, str]:
    """VOICEVOX：本地引擎已下载且运行中 -> 可用。"""
    from .voicevox_manager import VoiceVoxManager

    mgr = VoiceVoxManager()
    st = mgr.status()
    if st["state"] == "downloaded" and not st["running"]:
        return False, "已下载，未启动（在设置中启动）"
    if st["state"] == "missing":
        return False, "未下载引擎（在设置中一键下载）"
    if st["state"] == "downloading":
        return False, "引擎下载中…"
    if st["running"]:
        return True, "引擎运行中"
    return False, "引擎状态异常"


def _status_gpt_sovits(block: dict, engine: str) -> tuple[bool, str]:
    sub = _sub_block(block, engine)
    url = sub.get("api_url")
    if is_placeholder(url):
        return False, "缺少 GPT-SoVITS 服务地址"
    if not _service_online(url):
        return False, "GPT-SoVITS 服务未在线（需先启动本地服务）"
    return True, "服务在线"


# --------------------------------------------------------------------------- #
# 引擎目录定义
# --------------------------------------------------------------------------- #
StatusFn = Callable[[dict, str], tuple[bool, str]]


def _engine(
    zh: str, kind: str, desc: str, fields: list, status: StatusFn, voice_field: Optional[str] = None
) -> dict:
    return {
        "zh": zh,
        "kind": kind,
        "desc": desc,
        "fields": fields,
        "status": status,
        "voice_field": voice_field,
    }


TTS_CATALOG: dict[str, dict] = {
    "siliconflow_tts": _engine(
        "硅基流动 TTS", "cloud", "免费云端中文/英文语音（当前默认）",
        [_field("api_key", "API Key", "password", "sk-…"),
         _field("default_model", "模型", "text", "fnlp/MOSS-TTSD-v0.5"),
         _field("default_voice", "默认音色", "text", "fnlp/MOSS-TTSD-v0.5:diana")],
        _status_key, voice_field="default_voice",
    ),
    "edge_tts": _engine(
        "微软 Edge 语音", "cloud", "微软免费云端语音，音色极多（中/英/日/韩）",
        [_field("voice", "音色", "text", "zh-CN-XiaoxiaoNeural")],
        _status_always, voice_field="voice",
    ),
    "azure_tts": _engine(
        "Azure 语音", "cloud", "微软商用语音合成",
        [_field("api_key", "API Key", "password"),
         _field("region", "区域", "text", "eastus"),
         _field("voice", "音色", "text", "zh-CN-XiaoxiaoNeural")],
        _status_key, voice_field="voice",
    ),
    "openai_tts": _engine(
        "OpenAI 语音", "cloud", "OpenAI TTS（兼容端点）",
        [_field("api_key", "API Key", "password"),
         _field("base_url", "接口地址", "text", "https://api.openai.com/v1"),
         _field("model", "模型", "text", "gpt-4o-mini-tts"),
         _field("voice", "音色", "text", "alloy")],
        _status_key, voice_field="voice",
    ),
    "elevenlabs_tts": _engine(
        "ElevenLabs", "cloud", "高质量多语种语音（需付费 key）",
        [_field("api_key", "API Key", "password"),
         _field("voice_id", "音色 ID", "text"),
         _field("model_id", "模型", "text", "eleven_multilingual_v2")],
        _status_key, voice_field="voice_id",
    ),
    "minimax_tts": _engine(
        "MiniMax 语音", "cloud", "MiniMax 海螺语音",
        [_field("api_key", "API Key", "password"),
         _field("group_id", "Group ID", "text"),
         _field("model", "模型", "text", "speech-02-turbo"),
         _field("voice_id", "音色 ID", "text", "female-shaonv")],
        _status_key, voice_field="voice_id",
    ),
    "cartesia_tts": _engine(
        "Cartesia", "cloud", "Cartesia Sonic 语音",
        [_field("api_key", "API Key", "password"),
         _field("voice_id", "音色 ID", "text"),
         _field("model_id", "模型", "text", "sonic-3")],
        _status_key, voice_field="voice_id",
    ),
    "fish_api_tts": _engine(
        "Fish Audio", "cloud", "Fish Audio 语音克隆",
        [_field("api_key", "API Key", "password"),
         _field("reference_id", "参考音色 ID", "text"),
         _field("base_url", "接口地址", "text", "https://api.fish.audio")],
        _status_key, voice_field="reference_id",
    ),
    "spark_tts": _engine(
        "讯飞星火", "local_service", "SparkTTS 本地服务（默认 6006 端口）",
        [_field("api_url", "服务地址", "text", "http://127.0.0.1:6006/"),
         _field("gender", "性别", "text", "female"),
         _field("prompt_wav_upload", "参考音频 URL", "text")],
        _status_url, voice_field="prompt_wav_upload",
    ),
    "gpt_sovits_tts": _engine(
        "GPT-SoVITS", "local_service", "GPT-SoVITS 音色克隆（需 GPU 部署服务）",
        [_field("api_url", "服务地址", "text", "http://localhost:9880/tts"),
         _field("ref_audio_path", "参考音频路径", "text"),
         _field("prompt_text", "参考音频文本", "text"),
         _field("text_lang", "文本语言", "text", "all_ja")],
        _status_gpt_sovits, voice_field="ref_audio_path",
    ),
    "cosyvoice_tts": _engine(
        "CosyVoice", "local_service", "CosyVoice Gradio 服务",
        [_field("client_url", "服务地址", "text", "http://127.0.0.1:50000/"),
         _field("sft_dropdown", "音色", "text", "中文女"),
         _field("mode_checkbox_group", "模式", "text", "预训练音色")],
        lambda b, e: _status_url(b, e, "client_url"), voice_field="sft_dropdown",
    ),
    "cosyvoice2_tts": _engine(
        "CosyVoice2", "local_service", "CosyVoice2 Gradio 服务",
        [_field("client_url", "服务地址", "text", "http://127.0.0.1:50000/"),
         _field("sft_dropdown", "音色", "text", "中文女"),
         _field("mode_checkbox_group", "模式", "text", "预训练音色")],
        lambda b, e: _status_url(b, e, "client_url"), voice_field="sft_dropdown",
    ),
    "x_tts": _engine(
        "XTTS", "local_service", "XTTS 本地服务（默认 8020）",
        [_field("api_url", "服务地址", "text", "http://127.0.0.1:8020/tts_to_audio"),
         _field("speaker_wav", "说话人 WAV", "text", "female"),
         _field("language", "语言", "text", "en")],
        _status_url, voice_field="speaker_wav",
    ),
    "voicevox_tts": _engine(
        "VOICEVOX", "local_service", "本地日语语音引擎（萝莉音等，需下载引擎）",
        [_field("base_url", "引擎地址", "text", "http://127.0.0.1:50021"),
         _field("speaker", "音色 ID", "number", "3")],
        _status_voicevox, voice_field="speaker",
    ),
    "pyttsx3_tts": _engine(
        "Windows 语音", "builtin", "系统自带 TTS（零配置，音质机械）",
        [], _status_pyttsx3,
    ),
    "piper_tts": _engine(
        "Piper", "local", "本地 ONNX 语音（离线）",
        [_field("model_path", "模型文件", "text", "models/piper/zh_CN-huayan-medium.onnx"),
         _field("speaker_id", "说话人 ID", "number", "0")],
        _status_path, voice_field="model_path",
    ),
    "sherpa_onnx_tts": _engine(
        "Sherpa TTS", "local", "本地 sherpa-onnx VITS 语音",
        [_field("vits_model", "模型文件", "text"),
         _field("vits_tokens", "tokens 文件", "text")],
        lambda b, e: _status_path(b, e, "vits_model"), voice_field="vits_model",
    ),
    "melo_tts": _engine(
        "MeloTTS", "local", "本地 MeloTTS（需自行安装依赖）",
        [_field("speaker", "说话人", "text", "EN-Default"),
         _field("language", "语言", "text", "EN")],
        _status_manual_install, voice_field="speaker",
    ),
    "coqui_tts": _engine(
        "Coqui TTS", "local", "本地 Coqui（需自行安装依赖）",
        [_field("model_name", "模型名", "text", "tts_models/zh-CN/baker/tacotron2-DDC-GST"),
         _field("language", "语言", "text", "en")],
        _status_manual_install, voice_field="model_name",
    ),
    "bark_tts": _engine(
        "Bark", "local", "本地 Bark 语音（需自行安装依赖）",
        [_field("voice", "音色", "text", "v2/en_speaker_1")],
        _status_manual_install, voice_field="voice",
    ),
}

ASR_CATALOG: dict[str, dict] = {
    "sherpa_onnx_asr": _engine(
        "本地识别（内置）", "builtin", "内置 sherpa-onnx SenseVoice，离线免费",
        [], _status_sherpa_asr,
    ),
    "azure_asr": _engine(
        "Azure 语音识别", "cloud", "微软 Azure 语音识别",
        [_field("api_key", "API Key", "password"),
         _field("region", "区域", "text", "eastus"),
         _field("languages", "语言", "text", "en-US, zh-CN")],
        _status_key,
    ),
    "groq_whisper_asr": _engine(
        "Groq Whisper", "cloud", "Groq 高速 Whisper 识别",
        [_field("api_key", "API Key", "password"),
         _field("model", "模型", "text", "whisper-large-v3-turbo"),
         _field("lang", "语言（留空自动）", "text")],
        _status_key,
    ),
    "faster_whisper": _engine(
        "Faster-Whisper", "local", "本地 Whisper（需 pip install faster-whisper）",
        [_field("model_path", "模型", "text", "large-v3-turbo")],
        _status_manual_install,
    ),
}


def _evaluate(block: dict, engine: str, meta: dict) -> dict:
    try:
        ok, reason = meta["status"](block, engine)
    except Exception as e:  # 状态判定绝不可崩坏接口
        ok, reason = False, f"状态检测异常（{type(e).__name__}）"
    return {
        "key": engine,
        "zh": meta["zh"],
        "kind": meta["kind"],
        "desc": meta["desc"],
        "configured": bool(ok),
        "reason": reason,
        "fields": meta["fields"],
        "voice_field": meta.get("voice_field"),
    }


def get_engines(scope: str) -> list[dict]:
    """scope: 'tts' | 'asr'。返回带配置状态与字段 schema 的引擎列表。

    排序：已配置可用的引擎排前面，未配置的排后面；各组内部保持目录定义顺序
    （稳定排序，不改变 catalog 的相对次序）。
    """
    asr_block, tts_block = _raw_conf()
    block = tts_block if scope == "tts" else asr_block
    catalog = TTS_CATALOG if scope == "tts" else ASR_CATALOG
    engines = [_evaluate(block, k, v) for k, v in catalog.items()]
    engines.sort(key=lambda e: not e["configured"])
    return engines


def get_configured_engines(scope: str) -> list[dict]:
    """仅返回已配置可用的引擎（角色卡下拉用）。"""
    return [e for e in get_engines(scope) if e["configured"]]


def get_engine(scope: str, key: str) -> Optional[dict]:
    catalog = TTS_CATALOG if scope == "tts" else ASR_CATALOG
    meta = catalog.get(key)
    if meta is None:
        return None
    asr_block, tts_block = _raw_conf()
    block = tts_block if scope == "tts" else asr_block
    return _evaluate(block, key, meta)
