"""
角色台词集（原作台词 + 关键词触发）系統。

參考 anime-desktop-pet 的「原作台词集 + 关键词触发台词」机制（MIT，思路移植、原创实现）：
- 每角色一份 JSON（chat_history/<conf_uid>/quotes.json），分两类：
  * scenario_quotes：按场景（startup 启动 / click 点击 / idle 待机 / bye 告别）随机展示。
  * keyword_quotes：聊天命中关键词（如「天气」「晚安」「加油」）立即弹一句预设台词，
    然后 AI 照常回复（互不干扰）。
- 无档案时回传内置默认台词（通用，跨角色可用）。

纯 stdlib，路径安全復用 safe_join。
"""

from __future__ import annotations

import os
import json
import random
from typing import Dict, List, Optional

from loguru import logger

from .utils.path_safety import safe_join

CHAT_HISTORY_DIR = "chat_history"
QUOTES_FILE = "quotes.json"

# 内置默认台词（通用。可被每个角色的 quotes.json 覆盖）
_DEFAULT_SCENARIO_QUOTES: Dict[str, List[str]] = {
    "startup": [
        "你来了？我一直都在哦。",
        "欢迎回来，等你好久了。",
        "嘿，今天过得怎么样？",
    ],
    "click": [
        "嗯？叫我有什么事吗？",
        "嘿，别一直戳我啦。",
        "在呢在呢，怎么啦？",
        "戳我一下，心情好一点了吗？",
    ],
    "idle": [
        "好安静啊……要不要说点什么？",
        "我一直在看着你呢。",
        "有点无聊……陪我聊聊天嘛。",
    ],
    "bye": [
        "要走了吗？明天也要来哦。",
        "路上小心，我等你回来。",
        "再会啦，我会想你的。",
    ],
}

_DEFAULT_KEYWORD_QUOTES: Dict[str, List[str]] = {
    "天气": ["今天天气好像不错呢，要不要出去走走？", "下雨天待在家也不错呢。"],
    "晚安": ["晚安，做个好梦～", "晚安，明天见！"],
    "睡觉": ["困了就去睡吧，我会守着你的。", "晚安好梦～"],
    "加油": ["加油！你一定能做到的！", "我相信你，加油！"],
    "累": ["辛苦啦，先休息一下吧。", "累了吗？靠在我这边歇会儿。"],
    "饿": ["是不是饿了？记得吃点东西哦。", "肚子饿了吧？快去吃饭！"],
    "美食": ["听起来好好吃！", "下次也带我一起去吃嘛。"],
    "好吃": ["你吃得开心，我也很开心呢。"],
    "夸我": ["你最棒啦！", "当然啦，你本来就很厉害！"],
    "厉害": ["嘿嘿，你真的很厉害呢。"],
    "孤单": ["有我在呢，你不会孤单的。", "我一直陪着你哦。"],
    "孤独": ["别难过，我一直都在。"],
    "难过": ["别难过，有我在呢。", "难过的话，抱抱你。"],
    "伤心": ["别伤心了，我陪着你。"],
    "害怕": ["别怕，有我在。"],
    "谢谢": ["不客气～能帮到你我就很开心。"],
    "想你": ["我也想你！", "我也一直在想你哦。"],
    "爱你": ["我也爱你！", "最喜欢你啦。"],
}


def _path(conf_uid: str) -> str:
    return safe_join(CHAT_HISTORY_DIR, conf_uid, QUOTES_FILE)


def default_quotes() -> dict:
    """内置默认台词集（每调用回传一份深拷贝，避免外部改动污染默认值）。"""
    return {
        "scenario_quotes": {k: list(v) for k, v in _DEFAULT_SCENARIO_QUOTES.items()},
        "keyword_quotes": {k: list(v) for k, v in _DEFAULT_KEYWORD_QUOTES.items()},
    }


def load_quotes(conf_uid: str) -> dict:
    """读取角色台词集；无档案/损坏时回传默认集。"""
    default = default_quotes()
    try:
        p = _path(conf_uid)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # 缺的类别用默认补齐（向前兼容）
                for key in ("scenario_quotes", "keyword_quotes"):
                    if key not in data or not isinstance(data[key], dict):
                        data[key] = default[key]
                return data
    except Exception as e:
        logger.warning(f"[quotes] load failed for {conf_uid}: {e}")
    return default


def save_quotes(conf_uid: str, data: dict) -> bool:
    """原子写入角色台词集。"""
    try:
        p = _path(conf_uid)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = os.path.join(os.path.dirname(p), "." + QUOTES_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception as e:
        logger.warning(f"[quotes] save failed for {conf_uid}: {e}")
        return False


def random_quote(conf_uid: str, scenario: str) -> Optional[str]:
    """随机取一个场景台词；场景为空或没有台词时回传 None。"""
    data = load_quotes(conf_uid)
    pool = data.get("scenario_quotes", {}).get(scenario)
    if not pool:
        return None
    return random.choice(pool)


def keyword_trigger(conf_uid: str, user_text: str) -> Optional[str]:
    """聊天命中关键词时回传一句对应台词；未命中回传 None。

    多个关键词同时命中时，从所有命中集合里随机取一句。
    """
    if not user_text or not str(user_text).strip():
        return None
    text = str(user_text)
    data = load_quotes(conf_uid)
    kq = data.get("keyword_quotes", {})
    hits: List[str] = []
    for keyword, lines in kq.items():
        if lines and keyword in text:
            hits.extend(lines)
    return random.choice(hits) if hits else None


def get_quotes(conf_uid: str) -> dict:
    """给管理界面读取用（直接回传完整结构）。"""
    return load_quotes(conf_uid)


def update_quotes(conf_uid: str, scenario_quotes: dict, keyword_quotes: dict) -> bool:
    """覆盖更新某角色的台词集（保留缺失类别）。"""
    data = load_quotes(conf_uid)
    if isinstance(scenario_quotes, dict):
        data["scenario_quotes"] = scenario_quotes
    if isinstance(keyword_quotes, dict):
        data["keyword_quotes"] = keyword_quotes
    return save_quotes(conf_uid, data)


def reset_quotes(conf_uid: str) -> bool:
    """恢复默认台词集（写回内置默认）。"""
    return save_quotes(conf_uid, default_quotes())
