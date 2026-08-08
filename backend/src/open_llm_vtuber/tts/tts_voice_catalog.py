"""Per-engine TTS voice catalogs for the character-card voice picker.

每个引擎一个 provider，返回统一的目录结构：
    {
        "engine": "edge_tts",
        "mode": "list",                      # "list" = 下拉列表; "input" = 文本输入
        "voices": [{"value", "label"}],      # mode="list" 时使用
        "input": {"field", "label", "hint"}  # mode="input" 时使用
    }

策略：
- Group A（有固定/可查询音色集的引擎）：提供下拉列表。云端引擎在配置了 API key
  时实时查询，否则 fallback 到精选列表或输入框。
- Group B（参考音频/克隆/本地模型引擎）：没有离散音色集，mode="input" 由用户填
  （音色字段名 + 提示）。
- 所有网络查询都有短超时，任何失败都降级，绝不抛异常到路由层。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from typing import Optional

from loguru import logger

from ..config_manager.utils import read_yaml

# --------------------------------------------------------------------------- #
# Engine -> character-config voice field mapping
# --------------------------------------------------------------------------- #
# 角色卡保存时，把通用 "voice" 值写到该引擎子块的哪个字段。
ENGINE_VOICE_FIELD: dict[str, Optional[str]] = {
    "azure_tts": "voice",
    "bark_tts": "voice",
    "edge_tts": "voice",
    "cosyvoice_tts": "sft_dropdown",
    "cosyvoice2_tts": "sft_dropdown",
    "melo_tts": "speaker",
    "x_tts": "speaker_wav",
    "gpt_sovits_tts": "ref_audio_path",
    "siliconflow_tts": "default_voice",
    "coqui_tts": "model_name",
    "fish_api_tts": "reference_id",
    "minimax_tts": "voice_id",
    "sherpa_onnx_tts": "sid",
    "openai_tts": "voice",
    "spark_tts": "prompt_wav_upload",
    "elevenlabs_tts": "voice_id",
    "cartesia_tts": "voice_id",
    "piper_tts": "model_path",
    "voicevox_tts": "speaker",
    "pyttsx3_tts": None,  # 无音色参数
}

# 哪些引擎的 voice 字段是整数（角色卡发字符串，后端转 int）。
ENGINE_INT_VOICE: set[str] = {"voicevox_tts", "sherpa_onnx_tts", "piper_tts"}

# 全部引擎（含 pyttsx3_tts），供路由层做白名单校验。
ALL_ENGINES = sorted(ENGINE_VOICE_FIELD)


# --------------------------------------------------------------------------- #
# 音色中文翻译（下拉列表 label 追加「（中文）」后缀；value 不变，只影响显示）
# --------------------------------------------------------------------------- #

def _with_zh(label: str, zh: str) -> str:
    """在 label 末尾追加（中文翻译）；zh 为空或已含于 label 时原样返回。"""
    if not zh or zh in label:
        return label
    return f"{label}（{zh}）"

# 语言代码 → 中文（用于实时音色列表的语言标注）
LOCALE_ZH = {
    "zh-CN": "简体中文", "zh-TW": "繁體中文", "zh-HK": "粵語（香港）",
    "ja-JP": "日語", "ko-KR": "韓語",
    "en-US": "美式英語", "en-GB": "英式英語", "en-AU": "澳式英語",
    "en-CA": "加拿大英語", "en-IE": "愛爾蘭英語",
    "fr-FR": "法語", "fr-CA": "加拿大法語", "de-DE": "德語",
    "it-IT": "義大利語", "es-ES": "西班牙語", "es-MX": "墨西哥西班牙語",
    "pt-BR": "巴西葡萄牙語", "pt-PT": "葡萄牙語", "ru-RU": "俄語",
    "tr-TR": "土耳其語", "ar-EG": "阿拉伯語", "hi-IN": "印地語",
    "th-TH": "泰語", "vi-VN": "越南語", "id-ID": "印尼語",
    "pl-PL": "波蘭語", "nl-NL": "荷蘭語", "sv-SE": "瑞典語",
    "da-DK": "丹麥語", "fi-FI": "芬蘭語", "nb-NO": "挪威語",
    "cs-CZ": "捷克語", "uk-UA": "烏克蘭語", "ms-MY": "馬來語",
}

GENDER_ZH = {"Female": "女声", "Male": "男声", "Neutral": "中性"}

# edge-tts / Azure 常用音色 → 中文（value → 中文名・语言・性别）
EDGE_VOICE_ZH = {
    # 简体中文
    "zh-CN-XiaoxiaoNeural": "晓晓·简体中文·女声",
    "zh-CN-XiaoyiNeural": "晓伊·简体中文·女声",
    "zh-CN-YunjianNeural": "云健·简体中文·男声",
    "zh-CN-YunxiNeural": "云希·简体中文·男声",
    "zh-CN-YunxiaNeural": "云夏·简体中文·男声",
    "zh-CN-YunyangNeural": "云扬·简体中文·男声",
    "zh-CN-XiaochenNeural": "晓辰·简体中文·女声",
    "zh-CN-XiaohanNeural": "晓涵·简体中文·女声",
    "zh-CN-XiaomengNeural": "晓梦·简体中文·女声",
    "zh-CN-XiaomoNeural": "晓墨·简体中文·女声",
    "zh-CN-XiaoqiuNeural": "晓秋·简体中文·女声",
    "zh-CN-XiaoruiNeural": "晓睿·简体中文·女声",
    "zh-CN-XiaoshuangNeural": "晓双·简体中文·童声",
    "zh-CN-XiaoxuanNeural": "晓萱·简体中文·女声",
    "zh-CN-XiaoyanNeural": "晓颜·简体中文·女声",
    "zh-CN-XiaoyouNeural": "晓悠·简体中文·女声",
    "zh-CN-XiaozhenNeural": "晓甄·简体中文·女声",
    "zh-CN-YunfengNeural": "云枫·简体中文·男声",
    "zh-CN-YunhaoNeural": "云皓·简体中文·男声",
    "zh-CN-YunzeNeural": "云泽·简体中文·男声",
    "zh-CN-YunfanNeural": "云帆·简体中文·男声",
    # 繁體中文 / 粵語
    "zh-TW-HsiaoChenNeural": "晓臻·繁體中文·女声",
    "zh-TW-HsiaoYuNeural": "晓雨·繁體中文·女声",
    "zh-TW-YunJheNeural": "云哲·繁體中文·男声",
    "zh-HK-HiuMaanNeural": "晓曼·粵語·女声",
    "zh-HK-HiuGaaiNeural": "晓佳·粵語·女声",
    "zh-HK-WanLungNeural": "云龙·粵語·男声",
    # 日語
    "ja-JP-NanamiNeural": "七海·日語·女声",
    "ja-JP-KeitaNeural": "圭太·日語·男声",
    "ja-JP-AoiNeural": "葵·日語·女声",
    "ja-JP-DaichiNeural": "大地·日語·男声",
    "ja-JP-MayuNeural": "麻由·日語·女声",
    "ja-JP-NaokiNeural": "直树·日語·男声",
    "ja-JP-ShioriNeural": "诗织·日語·女声",
    # 韓語
    "ko-KR-SunHiNeural": "宣熙·韓語·女声",
    "ko-KR-InJoonNeural": "仁俊·韓語·男声",
    "ko-KR-JiMinNeural": "志敏·韓語·女声",
    "ko-KR-SeoHyeonNeural": "瑞贤·韓語·女声",
    "ko-KR-SoonBokNeural": "顺福·韓語·女声",
    # 英語
    "en-US-AvaNeural": "爱娃·美式英语·女声",
    "en-US-AndrewNeural": "安德鲁·美式英语·男声",
    "en-US-EmmaNeural": "艾玛·美式英语·女声",
    "en-US-BrianNeural": "布莱恩·美式英语·男声",
    "en-US-JennyNeural": "珍妮·美式英语·女声",
    "en-US-GuyNeural": "盖伊·美式英语·男声",
    "en-US-AriaNeural": "艾丽娅·美式英语·女声",
    "en-US-DavisNeural": "戴维斯·美式英语·男声",
    "en-US-JaneNeural": "简·美式英语·女声",
    "en-US-JasonNeural": "杰森·美式英语·男声",
    "en-US-SaraNeural": "莎拉·美式英语·女声",
    "en-US-TonyNeural": "托尼·美式英语·男声",
    "en-US-NancyNeural": "南希·美式英语·女声",
    "en-US-RogerNeural": "罗杰·美式英语·男声",
    "en-US-AmberNeural": "安珀·美式英语·女声",
    "en-US-AnaNeural": "安娜·美式英语·女声",
    "en-US-AshleyNeural": "阿什莉·美式英语·女声",
    "en-US-ChristopherNeural": "克里斯托弗·美式英语·男声",
    "en-US-CoraNeural": "科拉·美式英语·女声",
    "en-US-ElizabethNeural": "伊丽莎白·美式英语·女声",
    "en-US-EricNeural": "埃里克·美式英语·男声",
    "en-US-MichelleNeural": "米歇尔·美式英语·女声",
    "en-US-MonicaNeural": "莫妮卡·美式英语·女声",
    "en-US-JacobNeural": "雅各布·美式英语·男声",
    "en-US-MasonNeural": "梅森·美式英语·男声",
    "en-GB-SoniaNeural": "索尼娅·英式英语·女声",
    "en-GB-ThomasNeural": "托马斯·英式英语·男声",
    "en-GB-LibbyNeural": "丽比·英式英语·女声",
    "en-GB-RyanNeural": "瑞安·英式英语·男声",
    # 主要歐洲語言
    "fr-FR-DeniseNeural": "丹妮丝·法語·女声",
    "fr-FR-HenriNeural": "亨利·法語·男声",
    "de-DE-KatjaNeural": "卡佳·德語·女声",
    "de-DE-ConradNeural": "康拉德·德語·男声",
    "es-ES-ElviraNeural": "埃尔维拉·西班牙語·女声",
    "es-ES-AlvaroNeural": "阿尔瓦罗·西班牙語·男声",
    "it-IT-ElsaNeural": "艾尔莎·義大利語·女声",
    "it-IT-IsabellaNeural": "伊莎贝拉·義大利語·女声",
    "pt-BR-FranciscaNeural": "弗朗西斯卡·巴西葡萄牙語·女声",
    "pt-BR-AntonioNeural": "安东尼奥·巴西葡萄牙語·男声",
    "ru-RU-SvetlanaNeural": "斯韦特兰娜·俄語·女声",
    "ru-RU-DmitryNeural": "德米特里·俄語·男声",
    "pl-PL-ZofiaNeural": "佐菲娅·波蘭語·女声",
    "tr-TR-EmelNeural": "埃梅尔·土耳其語·女声",
    "ar-EG-SalmaNeural": "萨尔玛·阿拉伯語·女声",
}

# VOICEVOX 常用角色 → 中文
VOICEVOX_SPEAKER_ZH = {
    "ずんだもん": "毛豆团子",
    "四国めたん": "四国梅丹",
    "春日部つむぎ": "春日部纺",
    "波音リツ": "波音律",
    "雨晴はう": "雨晴",
    "冥鳴ひまり": "冥鸣阳鞠",
    "九州そら": "九州空",
    "もち子さん": "麻糬子",
    "剣崎めぐみ": "剑崎惠",
    "謳うアッカ": "吟唱阿卡",
    "アイギス": "埃癸斯",
    "デフォ子": "默认子",
    "ロイヤル・フォーチュン": "皇家幸运",
    "青山龍星": "青山龙星",
    "白上虎太郎": "白上虎太郎",
}

# VOICEVOX 风格 → 中文
VOICEVOX_STYLE_ZH = {
    "ノーマル": "普通", "あまあま": "甜甜", "ツンデレ": "傲娇",
    "セクシー": "性感", "ささやき": "低语", "ヒソヒソ": "窃窃私语",
    "叫び": "喊叫", "怒り": "愤怒", "喜び": "喜悦", "悲しみ": "悲伤",
    "落ち着き": "沉着", "うれしい": "开心", "かなしい": "伤心",
    "びえーん": "大哭", "てれあま": "害羞甜腻", "無念": "遗憾",
    "泣き": "哭泣", "甘やかし": "宠溺", "辛口": "毒舌",
    "マザー": "母性", "ウザお": "烦人", "笑い": "笑", "吐息": "吐息",
    "高圧的": "高压", "怖い": "可怕", "包み込むような": "温柔包裹",
    "ジト目": "死鱼眼", "情熱的": "热情", "方言": "方言",
    "増し": "加强", "レクイエム": "安魂曲", "猫": "猫",
}


# --------------------------------------------------------------------------- #
# 精选音色表（无需凭证）
# --------------------------------------------------------------------------- #

CURATED_VOICES = [
    # en（English）
    {"value": "en-US-AvaNeural", "label": "Ava（English US・F）（爱娃·美式英语·女声）", "locale": "en-US", "gender": "Female"},
    {"value": "en-US-AndrewNeural", "label": "Andrew（English US・M）（安德鲁·美式英语·男声）", "locale": "en-US", "gender": "Male"},
    {"value": "en-GB-SoniaNeural", "label": "Sonia（English UK・F）（索尼娅·英式英语·女声）", "locale": "en-GB", "gender": "Female"},
    {"value": "en-US-AshleyNeural", "label": "Ashley（English US・F）（阿什莉·美式英语·女声）", "locale": "en-US", "gender": "Female"},
]

# zh-TW 优先（UI 默认繁体中文）
CURATED_EDGE_VOICES = [
    {"value": "zh-TW-HsiaoChenNeural", "label": "曉臻（繁體中文・F）（晓臻·女声）", "locale": "zh-TW", "gender": "Female"},
    {"value": "zh-TW-HsiaoYuNeural", "label": "曉雨（繁體中文・F）（晓雨·女声）", "locale": "zh-TW", "gender": "Female"},
    {"value": "zh-TW-YunJheNeural", "label": "雲哲（繁體中文・M）（云哲·男声）", "locale": "zh-TW", "gender": "Male"},
    {"value": "zh-CN-XiaoxiaoNeural", "label": "曉曉（简体中文・F）（晓晓·女声）", "locale": "zh-CN", "gender": "Female"},
    {"value": "zh-CN-YunxiNeural", "label": "雲希（简体中文・M）（云希·男声）", "locale": "zh-CN", "gender": "Male"},
    {"value": "ja-JP-NanamiNeural", "label": "七海（日本語・F）（七海·日语·女声）", "locale": "ja-JP", "gender": "Female"},
    {"value": "ja-JP-KeitaNeural", "label": "圭太（日本語・M）（圭太·日语·男声）", "locale": "ja-JP", "gender": "Male"},
    {"value": "ko-KR-SunHiNeural", "label": "선히（한국어・F）（宣熙·韩语·女声）", "locale": "ko-KR", "gender": "Female"},
    {"value": "en-US-AvaNeural", "label": "Ava（English US・F）（爱娃·美式英语·女声）", "locale": "en-US", "gender": "Female"},
    {"value": "en-US-AndrewNeural", "label": "Andrew（English US・M）（安德鲁·美式英语·男声）", "locale": "en-US", "gender": "Male"},
    {"value": "en-GB-SoniaNeural", "label": "Sonia（English UK・F）（索尼娅·英式英语·女声）", "locale": "en-GB", "gender": "Female"},
]

AZURE_VOICES = [
    {"value": "zh-CN-XiaoxiaoNeural", "label": "曉曉（简体中文・F）（晓晓·女声）"},
    {"value": "zh-CN-YunxiNeural", "label": "雲希（简体中文・M）（云希·男声）"},
    {"value": "zh-TW-HsiaoChenNeural", "label": "曉臻（繁體中文・F）（晓臻·女声）"},
    {"value": "ja-JP-NanamiNeural", "label": "七海（日本語・F）（七海·日语·女声）"},
    {"value": "en-US-AriaNeural", "label": "Aria（English US・F）（艾丽娅·美式英语·女声）"},
    {"value": "en-US-GuyNeural", "label": "Guy（English US・M）（盖伊·美式英语·男声）"},
    {"value": "en-GB-SoniaNeural", "label": "Sonia（English UK・F）（索尼娅·英式英语·女声）"},
]

BARK_VOICES = [
    {"value": "v2/en_speaker_0", "label": "English speaker 0（英语说话人 0）"},
    {"value": "v2/en_speaker_1", "label": "English speaker 1（英语说话人 1）"},
    {"value": "v2/en_speaker_2", "label": "English speaker 2（英语说话人 2）"},
    {"value": "v2/en_speaker_3", "label": "English speaker 3（英语说话人 3）"},
    {"value": "v2/en_speaker_4", "label": "English speaker 4（英语说话人 4）"},
    {"value": "v2/en_speaker_5", "label": "English speaker 5（英语说话人 5）"},
    {"value": "v2/en_speaker_6", "label": "English speaker 6（英语说话人 6）"},
    {"value": "v2/en_speaker_7", "label": "English speaker 7（英语说话人 7）"},
    {"value": "v2/en_speaker_8", "label": "English speaker 8（英语说话人 8）"},
    {"value": "v2/en_speaker_9", "label": "English speaker 9（英语说话人 9）"},
    {"value": "v2/zh_speaker_0", "label": "Chinese speaker 0（中文说话人 0）"},
    {"value": "v2/zh_speaker_1", "label": "Chinese speaker 1（中文说话人 1）"},
    {"value": "v2/zh_speaker_2", "label": "Chinese speaker 2（中文说话人 2）"},
    {"value": "v2/zh_speaker_3", "label": "Chinese speaker 3（中文说话人 3）"},
]

MELO_VOICES = [
    {"value": "EN-Default", "label": "English Default（英语·默认）"},
    {"value": "EN-US", "label": "English US（英语·美式）"},
    {"value": "EN-BR", "label": "English Brazil（英语·巴西）"},
    {"value": "EN-INDIA", "label": "English India（英语·印度）"},
    {"value": "EN-AU", "label": "English Australia（英语·澳式）"},
    {"value": "ZH", "label": "简体中文（简体中文）"},
    {"value": "JP", "label": "日本語（日语）"},
    {"value": "KR", "label": "한국어（韩语）"},
    {"value": "ES", "label": "Español（西班牙语）"},
    {"value": "FR", "label": "Français（法语）"},
    {"value": "DE", "label": "Deutsch（德语）"},
]

OPENAI_VOICES = [
    {"value": "alloy", "label": "alloy（合金）"},
    {"value": "echo", "label": "echo（回声）"},
    {"value": "fable", "label": "fable（寓言）"},
    {"value": "onyx", "label": "onyx（黑玛瑙）"},
    {"value": "nova", "label": "nova（新星）"},
    {"value": "shimmer", "label": "shimmer（微光）"},
    # kokoro 等兼容服务器的自定义音色可手动填
]

MINIMAX_PRESET_VOICES = [
    {"value": "male-qn-qingse", "label": "男声-青年（qingse）"},
    {"value": "male-qn-jingying", "label": "男声-精英（jingying）"},
    {"value": "male-qn-badao", "label": "男声-霸道（badao）"},
    {"value": "male-qn-tianmei", "label": "男声-甜美（tianmei）"},
    {"value": "female-shaonv", "label": "女声-少女（shaonv）"},
    {"value": "female-yujie", "label": "女声-御姐（yujie）"},
    {"value": "female-chengshu", "label": "女声-成熟（chengshu）"},
    {"value": "female-tianmei", "label": "女声-甜美（tianmei）"},
]

SILICONFLOW_PRESET_VOICES = [
    {"value": "fnlp/MOSS-TTSD-v0.5:anna", "label": "MOSS-TTSD・anna（女声）（安娜）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:alex", "label": "MOSS-TTSD・alex（男声）（亚历克斯）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:bella", "label": "MOSS-TTSD・bella（贝拉）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:benjamin", "label": "MOSS-TTSD・benjamin（本杰明）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:charles", "label": "MOSS-TTSD・charles（查尔斯）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:claire", "label": "MOSS-TTSD・claire（克莱尔）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:david", "label": "MOSS-TTSD・david（大卫）"},
    {"value": "fnlp/MOSS-TTSD-v0.5:diana", "label": "MOSS-TTSD・diana（女声）（黛安娜）"},
]

VOICEVOX_FALLBACK_SPEAKERS = [
    {"value": "3", "label": "ずんだもん（ノーマル）（毛豆团子·普通）"},
    {"value": "4", "label": "ずんだもん（あまあま）（毛豆团子·甜甜）"},
    {"value": "0", "label": "四国めたん（四国梅丹）"},
    {"value": "8", "label": "春日部つむぎ（春日部纺）"},
]

_NET_TIMEOUT = 3.0


# --------------------------------------------------------------------------- #
# conf.yaml 读取（取某引擎子块的字段值）
# --------------------------------------------------------------------------- #

def _engine_block(engine: str) -> dict:
    """Read the base conf.yaml tts_config.<engine> sub-block (may be empty)."""
    try:
        data = read_yaml("conf.yaml") or {}
        tts = (data.get("character_config", {}) or {}).get("tts_config", {}) or {}
        return (tts.get(engine) or {}) if isinstance(tts.get(engine), dict) else {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Live voice queries（网络，均带短超时 + 失败降级）
# --------------------------------------------------------------------------- #

async def _live_edge_voices() -> Optional[list]:
    """edge-tts 实时音色列表（edge_tts.list_voices），失败返回 None。"""
    try:
        import edge_tts

        raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=6.0)
        voices = []
        for v in raw:
            short = v.get("ShortName")
            if not short:
                continue
            base = f"{v.get('FriendlyName') or short}（{v.get('Locale', '')}・{v.get('Gender', '')}）"
            zh = EDGE_VOICE_ZH.get(short)
            if not zh:
                lang = LOCALE_ZH.get(v.get("Locale", ""), "")
                gender = GENDER_ZH.get(v.get("Gender", ""), "")
                zh = "·".join(x for x in (lang, gender) if x)
            voices.append(
                {
                    "value": short,
                    "label": _with_zh(base, zh),
                    "locale": v.get("Locale", ""),
                    "gender": v.get("Gender", ""),
                }
            )
        voices.sort(key=lambda x: (not x["locale"].startswith("zh-TW"), x["locale"]))
        return voices or None
    except Exception as e:
        logger.debug(f"live edge_tts.list_voices failed: {type(e).__name__}")
        return None


async def _live_voicevox(base_url: str) -> Optional[list]:
    """查询本地 VOICEVOX 引擎 /speakers -> [{"value": style_id, "label": ...}]。"""
    try:
        import json
        import urllib.request

        url = f"{base_url.rstrip('/')}/speakers"
        with urllib.request.urlopen(url, timeout=_NET_TIMEOUT) as resp:
            speakers = json.loads(resp.read().decode("utf-8"))
        voices = []
        for spk in speakers:
            spk_name = spk.get("name", "")
            spk_zh = VOICEVOX_SPEAKER_ZH.get(spk_name, "")
            for style in spk.get("styles", []):
                sid = style.get("id")
                if sid is None:
                    continue
                style_name = style.get("name", "")
                zh_parts = [spk_zh] if spk_zh and spk_zh != spk_name else []
                style_zh = VOICEVOX_STYLE_ZH.get(style_name, "")
                if style_zh and style_zh != style_name:
                    zh_parts.append(style_zh)
                base = f"{spk_name}・{style_name}"
                voices.append(
                    {
                        "value": str(sid),
                        "label": _with_zh(base, "·".join(zh_parts)),
                    }
                )
        return voices or None
    except Exception as e:
        logger.debug(f"live voicevox speakers failed: {type(e).__name__}")
        return None


async def _live_elevenlabs(api_key: str) -> Optional[list]:
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
        )
        with urllib.request.urlopen(req, timeout=_NET_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        voices = [
            {"value": v.get("voice_id", ""), "label": v.get("name", v.get("voice_id", ""))}
            for v in data.get("voices", [])
            if v.get("voice_id")
        ]
        return voices or None
    except Exception as e:
        logger.debug(f"live elevenlabs voices failed: {type(e).__name__}")
        return None


async def _live_minimax(group_id: str, api_key: str) -> Optional[list]:
    try:
        import json
        import urllib.request

        url = f"https://api.minimax.chat/v1/voice/list?GroupId={group_id}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=_NET_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        voices = [
            {"value": v.get("voice_id", ""), "label": v.get("voice_name", v.get("voice_id", ""))}
            for v in data.get("voice_list", [])
            if v.get("voice_id")
        ]
        return voices or None
    except Exception as e:
        logger.debug(f"live minimax voices failed: {type(e).__name__}")
        return None


async def _live_fish(api_key: str, base_url: str) -> Optional[list]:
    try:
        import json
        import urllib.request

        url = f"{base_url.rstrip('/')}/v1/tts/references?page_size=100"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=_NET_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        voices = [
            {"value": it.get("id", ""), "label": it.get("title", it.get("id", ""))}
            for it in data.get("items", [])
            if it.get("id")
        ]
        return voices or None
    except Exception as e:
        logger.debug(f"live fish references failed: {type(e).__name__}")
        return None


async def _live_cartesia(api_key: str) -> Optional[list]:
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            "https://api.cartesia.ai/voices",
            headers={"X-API-Key": api_key},
        )
        with urllib.request.urlopen(req, timeout=_NET_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        voices = [
            {"value": v.get("id", ""), "label": v.get("name", v.get("id", ""))}
            for v in data.get("voices", [])
            if v.get("id")
        ]
        return voices or None
    except Exception as e:
        logger.debug(f"live cartesia voices failed: {type(e).__name__}")
        return None


# --------------------------------------------------------------------------- #
# Providers（每个引擎一个 async 函数，永不抛异常）
# --------------------------------------------------------------------------- #

async def _edge_provider() -> dict:
    live = await _live_edge_voices()
    return {
        "engine": "edge_tts",
        "mode": "list",
        "voices": live or CURATED_EDGE_VOICES,
        "input": None,
    }


async def _voicevox_provider() -> dict:
    block = _engine_block("voicevox_tts")
    base_url = str(block.get("base_url") or "http://127.0.0.1:50021")
    live = await _live_voicevox(base_url)
    return {
        "engine": "voicevox_tts",
        "mode": "list",
        "voices": live or VOICEVOX_FALLBACK_SPEAKERS,
        "input": None,
    }


async def _elevenlabs_provider() -> dict:
    block = _engine_block("elevenlabs_tts")
    api_key = str(block.get("api_key") or "").strip()
    live = await _live_elevenlabs(api_key) if api_key else None
    if live:
        return {"engine": "elevenlabs_tts", "mode": "list", "voices": live, "input": None}
    return _input_catalog(
        "elevenlabs_tts", "voice_id", "ElevenLabs 语音 ID", "填 ElevenLabs 控制台的 Voice ID；配置 api_key 后会自动拉取列表"
    )


async def _minimax_provider() -> dict:
    block = _engine_block("minimax_tts")
    api_key = str(block.get("api_key") or "").strip()
    group_id = str(block.get("group_id") or "").strip()
    live = await _live_minimax(group_id, api_key) if api_key and group_id else None
    return {
        "engine": "minimax_tts",
        "mode": "list",
        "voices": live or MINIMAX_PRESET_VOICES,
        "input": None,
    }


async def _fish_provider() -> dict:
    block = _engine_block("fish_api_tts")
    api_key = str(block.get("api_key") or "").strip()
    base_url = str(block.get("base_url") or "https://api.fish.audio")
    live = await _live_fish(api_key, base_url) if api_key else None
    if live:
        return {"engine": "fish_api_tts", "mode": "list", "voices": live, "input": None}
    return _input_catalog(
        "fish_api_tts", "reference_id", "Fish Audio 参考 ID", "从 fish.audio 网站获取参考 ID；配置 api_key 后会自动拉取"
    )


async def _cartesia_provider() -> dict:
    block = _engine_block("cartesia_tts")
    api_key = str(block.get("api_key") or "").strip()
    live = await _live_cartesia(api_key) if api_key else None
    if live:
        return {"engine": "cartesia_tts", "mode": "list", "voices": live, "input": None}
    return _input_catalog(
        "cartesia_tts", "voice_id", "Cartesia 语音 ID", "填 Cartesia 控制台的 Voice ID；配置 api_key 后会自动拉取"
    )


async def _siliconflow_provider() -> dict:
    return {"engine": "siliconflow_tts", "mode": "list", "voices": SILICONFLOW_PRESET_VOICES, "input": None}


async def _azure_provider() -> dict:
    return {"engine": "azure_tts", "mode": "list", "voices": AZURE_VOICES, "input": None}


async def _bark_provider() -> dict:
    return {"engine": "bark_tts", "mode": "list", "voices": BARK_VOICES, "input": None}


async def _melo_provider() -> dict:
    return {"engine": "melo_tts", "mode": "list", "voices": MELO_VOICES, "input": None}


async def _openai_provider() -> dict:
    return {"engine": "openai_tts", "mode": "list", "voices": OPENAI_VOICES, "input": None}


# ---- Group B：参考音频 / 克隆引擎（mode=input）----

def _input_catalog(engine: str, field: str, label: str, hint: str) -> dict:
    return {"engine": engine, "mode": "input", "voices": [], "input": {"field": field, "label": label, "hint": hint}}


async def _gpt_sovits_provider() -> dict:
    return _input_catalog("gpt_sovits_tts", "ref_audio_path", "参考音频路径", "填 GPT-SoVITS 参考音频的本地路径（决定音色）")


async def _x_tts_provider() -> dict:
    return _input_catalog("x_tts", "speaker_wav", "说话人 WAV", "填 XTTS 说话人参考音频路径或名称")


async def _coqui_provider() -> dict:
    return _input_catalog("coqui_tts", "model_name", "模型名称", "如 tts_models/zh-CN/baker/tacotron2-DDC-GST")


async def _spark_provider() -> dict:
    return _input_catalog("spark_tts", "prompt_wav_upload", "参考音频 URL/路径", "voice_clone 模式需要参考音频；voice_creation 模式用性别生成")


async def _sherpa_provider() -> dict:
    return _input_catalog("sherpa_onnx_tts", "sid", "说话人 ID（数字）", "多说话人模型的 sid")


async def _piper_provider() -> dict:
    return _input_catalog("piper_tts", "model_path", "模型文件路径", "如 models/piper/zh_CN-huayan-medium.onnx")


async def _cosyvoice_provider(engine: str) -> dict:
    return _input_catalog(engine, "sft_dropdown", "预训练音色（sft_dropdown）", "如 中文女 / 中文男 / 日语男 等")


async def _pyttsx3_provider() -> dict:
    return {"engine": "pyttsx3_tts", "mode": "list", "voices": [], "input": None}


# --------------------------------------------------------------------------- #
# Registry + entry point
# --------------------------------------------------------------------------- #

_PROVIDERS: dict[str, object] = {
    "azure_tts": _azure_provider,
    "bark_tts": _bark_provider,
    "edge_tts": _edge_provider,
    "cosyvoice_tts": lambda: _cosyvoice_provider("cosyvoice_tts"),
    "cosyvoice2_tts": lambda: _cosyvoice_provider("cosyvoice2_tts"),
    "melo_tts": _melo_provider,
    "x_tts": _x_tts_provider,
    "gpt_sovits_tts": _gpt_sovits_provider,
    "siliconflow_tts": _siliconflow_provider,
    "coqui_tts": _coqui_provider,
    "fish_api_tts": _fish_provider,
    "minimax_tts": _minimax_provider,
    "sherpa_onnx_tts": _sherpa_provider,
    "openai_tts": _openai_provider,
    "spark_tts": _spark_provider,
    "elevenlabs_tts": _elevenlabs_provider,
    "cartesia_tts": _cartesia_provider,
    "piper_tts": _piper_provider,
    "voicevox_tts": _voicevox_provider,
    "pyttsx3_tts": _pyttsx3_provider,
}


# --------------------------------------------------------------------------- #
# Preview availability（能否用真实引擎合成试听音频）
# --------------------------------------------------------------------------- #
# 决定 UI 是否显示可用的试听按钮。诚实标注：配置缺失 / 服务未启动的引擎
# 禁用试听并给出原因，而不是静默无声。

def _preview_info(engine: str) -> tuple[bool, str]:
    """Return (previewable, reason_if_not). 依据当前基础配置判断。"""
    block = _engine_block(engine)

    if engine == "edge_tts":
        return True, ""
    if engine == "siliconflow_tts":
        ok = bool(str(block.get("api_key") or "").strip())
        return ok, "" if ok else "需在 TTS 设置中配置 SiliconFlow API Key 后才能试听"
    if engine in ("azure_tts", "minimax_tts", "elevenlabs_tts", "cartesia_tts", "fish_api_tts", "openai_tts"):
        ok = bool(str(block.get("api_key") or "").strip())
        name = {"azure_tts": "Azure", "minimax_tts": "MiniMax", "elevenlabs_tts": "ElevenLabs",
                "cartesia_tts": "Cartesia", "fish_api_tts": "Fish Audio", "openai_tts": "OpenAI 兼容"}[engine]
        return ok, "" if ok else f"需在 TTS 设置中配置 {name} API Key 后才能试听"
    if engine == "voicevox_tts":
        return True, ""  # 本地引擎可用则能试听；失败时前端显示引擎不可用
    if engine in ("bark_tts", "melo_tts", "coqui_tts", "piper_tts", "sherpa_onnx_tts"):
        return True, ""  # 本地模型：能试听，但首次加载可能较慢
    if engine in ("gpt_sovits_tts", "x_tts", "cosyvoice_tts", "cosyvoice2_tts", "spark_tts"):
        return False, "需先启动对应的本地/远程 TTS 服务并配置参考音频，才能试听"
    if engine == "pyttsx3_tts":
        return False, "pyttsx3 没有可选音色，无法试听"
    return True, ""


async def get_voice_catalog(engine: str) -> dict:
    """返回某引擎的 voice catalog；未知引擎降级为 input 模式。"""
    provider = _PROVIDERS.get(engine)
    previewable, preview_note = _preview_info(engine)
    if provider is None:
        result = _input_catalog(
            engine, "voice", "音色", f"未知引擎 {engine}，手动填写音色"
        )
        result["previewable"] = previewable
        result["previewNote"] = preview_note
        return result
    try:
        result = await provider()
        if not isinstance(result, dict) or "mode" not in result:
            raise ValueError("provider returned invalid catalog")
        result["previewable"] = previewable
        result["previewNote"] = preview_note
        return result
    except Exception as e:
        logger.warning(f"voice catalog failed for {engine}: {type(e).__name__}")
        field = ENGINE_VOICE_FIELD.get(engine) or "voice"
        result = _input_catalog(engine, field, "音色", "获取音色列表失败，请手动填写")
        result["previewable"] = previewable
        result["previewNote"] = preview_note
        return result


# --------------------------------------------------------------------------- #
# Voice sample synthesis（試聽：用真实引擎 + 指定音色合成短句）
# --------------------------------------------------------------------------- #
# 复用 TTSFactory + 基础 conf.yaml 配置 + 覆盖的音色，让"听到的就是实际引擎的声音"。

_AUDIO_MEDIA = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "pcm": "audio/wav",
}

SAMPLE_TIMEOUT = 30.0  # 本地模型引擎（bark/melo/sherpa 等）首次加载可能很慢

# 试听缓存：首次合成保存到 backend/vendor/voice_samples/，之后同
# (engine, voice, text) 直接读缓存文件，避免重复等待本地/云端引擎。
# tts_voice_catalog.py 位于 backend/src/open_llm_vtuber/tts/ —— 上溯 4 层是 backend/
_SAMPLE_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "vendor", "voice_samples",
)
_SAMPLE_CACHE_MAX = 300
_SAMPLE_CACHE_EXTS = (".wav", ".mp3", ".ogg", ".opus")


def _sample_cache_key(engine: str, voice: str, text: str) -> str:
    return hashlib.md5(f"{engine}|{voice}|{text}".encode("utf-8")).hexdigest()


def _find_cached_sample(key: str) -> Optional[tuple[bytes, str]]:
    """查试听缓存；命中返回 (audio_bytes, ext)。"""
    try:
        if not os.path.isdir(_SAMPLE_CACHE_DIR):
            return None
        for name in os.listdir(_SAMPLE_CACHE_DIR):
            stem, ext = os.path.splitext(name)
            if stem == key and ext.lower() in _SAMPLE_CACHE_EXTS:
                with open(os.path.join(_SAMPLE_CACHE_DIR, name), "rb") as f:
                    return (f.read(), ext.lstrip("."))
    except Exception:
        pass
    return None


def _save_cached_sample(key: str, data: bytes, ext: str) -> None:
    try:
        os.makedirs(_SAMPLE_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_SAMPLE_CACHE_DIR, f"{key}{ext}"), "wb") as f:
            f.write(data)
        _prune_sample_cache()
    except Exception:
        pass


def _prune_sample_cache() -> None:
    """缓存文件超上限时按修改时间删除最旧的，避免无限增长。"""
    try:
        if not os.path.isdir(_SAMPLE_CACHE_DIR):
            return
        files = [
            os.path.join(_SAMPLE_CACHE_DIR, n) for n in os.listdir(_SAMPLE_CACHE_DIR)
            if os.path.splitext(n)[1].lower() in _SAMPLE_CACHE_EXTS
        ]
        if len(files) <= _SAMPLE_CACHE_MAX:
            return
        files.sort(key=os.path.getmtime)
        for old in files[: len(files) - _SAMPLE_CACHE_MAX]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception:
        pass


def media_type_for_ext(ext: str) -> str:
    return _AUDIO_MEDIA.get((ext or "mp3").lower().lstrip("."), "audio/mpeg")


async def synthesize_voice_sample(
    engine: str, voice: str, text: str
) -> Optional[tuple[bytes, str]]:
    """用真实引擎 + 指定音色合成一句试听音频（带本地缓存）。

    首次合成保存到 backend/vendor/voice_samples/，之后同 (engine, voice, text)
    直接读缓存文件返回，无需重复等待引擎。
    Returns (audio_bytes, file_ext) on success, None on any failure.
    永不抛异常到路由层。
    """
    key = _sample_cache_key(engine, voice, text)
    cached = _find_cached_sample(key)
    if cached:
        return cached
    try:
        from .tts_factory import TTSFactory

        config = dict(_engine_block(engine))
        field = ENGINE_VOICE_FIELD.get(engine)
        if field and voice:
            value: object = voice
            if engine in ENGINE_INT_VOICE:
                try:
                    value = int(str(voice))
                except (TypeError, ValueError):
                    value = voice
            config[field] = value

        tts = TTSFactory.get_tts_engine(engine, **config)
        file_name = f"preview_{uuid.uuid4().hex[:8]}"
        path = await asyncio.wait_for(
            tts.async_generate_audio(text=text, file_name_no_ext=file_name),
            timeout=SAMPLE_TIMEOUT,
        )
        if not path or not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            data = f.read()
        try:
            os.remove(path)
        except OSError:
            pass
        ext = os.path.splitext(path)[1] or "mp3"
        # 写缓存，后续同引擎+音色+文本直接读文件
        _save_cached_sample(key, data, ext)
        return (data, ext)
    except asyncio.TimeoutError:
        logger.warning(f"voice sample timeout for {engine}")
        return None
    except Exception as e:
        logger.warning(f"voice sample failed for {engine}: {type(e).__name__}: {e}")
        return None
