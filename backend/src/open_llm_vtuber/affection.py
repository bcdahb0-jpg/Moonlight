"""
好感度（Affection）系統。

參考 mea-pet-public 的 affection 模組（MIT，直接移植思路，原創實作）：
- 值域 0–100，初始 5。
- 每次真人對話後依「訊息長度」加好感度：<10 字 +1、<50 字 +2、其餘 +3。
- 每日上限（AFFECTION_DAILY_CAP=15），防止短時間刷滿。
- 7 級關係分層（陌生人→摯友），升階觸發 milestone，並把升階提示注入 system prompt，
  引導 LLM 語氣隨關係親疏變化。

儲存：每角色一份 JSON，放在 chat_history/<conf_uid>/affection.json（與 core_memory.md 同層，
路徑安全復用 safe_join）。純 stdlib，無外部依賴。
"""

from __future__ import annotations

import os
import json
from datetime import date
from typing import Optional, Tuple

from loguru import logger

from .utils.path_safety import safe_join

# Base directory every character's affection file is confined to.
CHAT_HISTORY_DIR = "chat_history"
AFFECTION_FILE = "affection.json"

# 好感度邊界與初始值
AFFECTION_MIN = 0
AFFECTION_MAX = 100
AFFECTION_INITIAL = 5
# 每日最多可獲得的好感度
AFFECTION_DAILY_CAP = 15

# 7 級關係分層：(門檻值, 關係名, 關係描述, 語氣提示)
# 依 mea-pet 的 tier 結構重寫成符合 Moonlight 角色的繁體中文口吻。
AFFECTION_TIERS: Tuple[Tuple[int, str, str, str], ...] = (
    (0, "陌生人", "你們剛剛認識，彼此還很陌生。", "語氣疏離、客氣，話少。"),
    (10, "認識", "已經有些交流，開始熟悉對方。", "語氣稍微放鬆，但仍保持禮貌。"),
    (30, "熟人", "彼此有一定了解，能自然相處。", "語氣親切，會開玩笑。"),
    (50, "朋友", "建立了信任的友誼關係。", "語氣真誠熱絡，願意分享心事。"),
    (70, "好朋友", "無話不談的知心好友。", "語氣親密，會主動關心對方。"),
    (85, "親密", "超越友誼的親密羈絆。", "語氣溫柔依賴，帶有佔有慾。"),
    (95, "摯友", "此生最珍視的存在。", "語氣深情忠誠，視對方為唯一。"),
)

# 各檔次升階時的短語（昇到該 tier 時的語氣引導）
_TIER_UPGRADE_LINES = {
    10: "我們好像開始熟起來了呢。",
    30: "嗯…和你的距離，好像又近了一點。",
    50: "你已經是我的朋友了，不准賴帳。",
    70: "我什麼都想跟你說。",
    85: "你…只能是我的人。",
    95: "這輩子，我都會陪著你。",
}


def _path(conf_uid: str) -> str:
    """Safe path to chat_history/<conf_uid>/affection.json (reject path escapes)."""
    return safe_join(CHAT_HISTORY_DIR, conf_uid, AFFECTION_FILE)


def _today_key() -> str:
    return date.today().isoformat()


def load_affection(conf_uid: str) -> dict:
    """讀取好感度狀態；檔案不存在或損壞時回傳預設。"""
    default = {
        "value": AFFECTION_INITIAL,
        "gained_today": 0,
        "today": _today_key(),
    }
    try:
        p = _path(conf_uid)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return default
            # 防呆：值夾到合法範圍
            val = int(data.get("value", AFFECTION_INITIAL))
            data["value"] = max(AFFECTION_MIN, min(AFFECTION_MAX, val))
            return data
    except Exception as e:
        # 壞檔視為無記憶，日誌記一筆即可
        logger.warning(f"[affection] load failed for {conf_uid}: {e}")
    return default


def _save_affection(conf_uid: str, data: dict) -> None:
    """原子寫入好感度 JSON（temp + os.replace，保留中文）。"""
    p = _path(conf_uid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = os.path.join(os.path.dirname(p), "." + AFFECTION_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def tier_for(value: int) -> tuple:
    """回傳目前所在 tier 的 (threshold, name, description, tone)。"""
    current = AFFECTION_TIERS[0]
    for tier in AFFECTION_TIERS:
        if value >= tier[0]:
            current = tier
        else:
            break
    return current


def delta_for_input(text: str) -> int:
    """依使用者訊息長度給好感度增量：<10 字 +1、<50 字 +2、其餘 +3。"""
    n = len(str(text or "").strip())
    if n < 10:
        return 1
    if n < 50:
        return 2
    return 3


def add_affection(conf_uid: str, delta: int) -> dict:
    """加好感度並套用每日上限。回傳更新後的狀態 dict。

    delta < 0 視為 0（目前只加不扣）。跨日自動重置當日累計。
    """
    data = load_affection(conf_uid)
    if data.get("today") != _today_key():
        data["today"] = _today_key()
        data["gained_today"] = 0

    delta = max(0, int(delta))
    room = AFFECTION_DAILY_CAP - data.get("gained_today", 0)
    if room <= 0:
        return data  # 今日已滿，不加也不寫

    applied = min(delta, room)
    data["value"] = max(AFFECTION_MIN, min(AFFECTION_MAX, data["value"] + applied))
    data["gained_today"] = data.get("gained_today", 0) + applied
    _save_affection(conf_uid, data)
    return data


def affection_context_block(conf_uid: str) -> str:
    """產生要注入 system prompt 的「與主人的關係」段落；回傳空字串表示無需注入。

    內含目前好感度、關係層級、語氣提示，以及「剛升階」時的角色引導句。
    """
    data = load_affection(conf_uid)
    value = data.get("value", AFFECTION_INITIAL)
    _, name, desc, tone = tier_for(value)
    block = (
        f"\n\n## 你與主人的關係\n"
        f"- 關係：{name}（好感度 {value}/100）\n"
        f"- 描述：{desc}\n"
        f"- 語氣：{tone}"
    )
    # 升階提示：只在這一輪升階時出現（由 apply_turn 寫入 milestone）。
    milestone = data.get("milestone")
    if milestone:
        block += f"\n- 剛升階：{milestone}"
    return block


def affection_summary(conf_uid: str) -> dict:
    """給前端顯示用的好感度摘要（不含內部計數細節）。"""
    data = load_affection(conf_uid)
    value = data.get("value", AFFECTION_INITIAL)
    _, name, desc, _tone = tier_for(value)
    next_tier = None
    for t in AFFECTION_TIERS:
        if t[0] > value:
            next_tier = {"name": t[1], "threshold": t[0]}
            break
    return {
        "value": value,
        "max": AFFECTION_MAX,
        "tier": name,
        "description": desc,
        "next": next_tier,
    }


def apply_turn(conf_uid: str, user_text: str) -> Optional[dict]:
    """一輪真人對話結束後呼叫：依文字長度加好感度，回傳本次狀態改變。

    回傳 dict 含 ``summary``（前端顯示用）與 ``milestone``（升階語，可能為 None）。
    主動搭話（proactive）不應呼叫此函式。
    """
    if not user_text or not str(user_text).strip():
        return None
    delta = delta_for_input(str(user_text))
    data = add_affection(conf_uid, delta)
    milestone = None
    # 判斷是否剛升階：只看 tier 門檻，避免每次重複提示
    current_threshold = tier_for(data.get("value", AFFECTION_INITIAL))[0]
    if current_threshold in _TIER_UPGRADE_LINES and data.get("value") >= current_threshold:
        # 用「里程碑已記錄」flag 避免同一個 tier 每輪都提示
        if data.get("last_milestone") != current_threshold:
            milestone = _TIER_UPGRADE_LINES[current_threshold]
            data["last_milestone"] = current_threshold
            data["milestone"] = milestone
            _save_affection(conf_uid, data)
    summary = affection_summary(conf_uid)
    return {"summary": summary, "milestone": milestone}
