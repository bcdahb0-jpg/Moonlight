"""游戏陪玩：内置游戏表（P4）。

每个游戏定义：窗口匹配正则（对 ScreenFrame.window.title/app 匹配）+ 事件
识别提示词（指导 LLM 从画面描述里识别高光/事件）。
参考：ZerolanLiveRobot（窗口匹配 + 游戏实况对话）、Airi（世界状态事件驱动）。
"""
from __future__ import annotations

import re
from typing import Optional

GAMES: list[dict] = [
    {
        "id": "minecraft",
        "name": "我的世界",
        "window_regex": r"minecraft|我的世界",
        "sample_sec": 10,
        "event_prompt": (
            "你是一个《我的世界》游戏观察员。根据画面描述识别游戏事件，"
            "只输出一个 JSON：{\"event_type\": \"kill|victory|death|drop|levelup|build|other\", "
            "\"confidence\": 0~1, \"summary\": \"8-20字中文描述\"}。"
            "击杀怪物→kill，打败 BOSS/获胜→victory，角色死亡→death，"
            "掉落重要物品→drop，升级→levelup，搭建完成→build，日常→other。"
        ),
    },
    {
        "id": "genshin",
        "name": "原神",
        "window_regex": r"原神|genshin",
        "sample_sec": 10,
        "event_prompt": (
            "你是一个《原神》游戏观察员。根据画面描述识别游戏事件，"
            "只输出一个 JSON：{\"event_type\": \"kill|victory|death|drop|levelup|opening|other\", "
            "\"confidence\": 0~1, \"summary\": \"8-20字中文描述\"}。"
            "击败精英/世界BOSS→kill，深渊/周本通关→victory，角色倒下→death，"
            "掉落圣遗物/材料→drop，升级→levelup，剧情对话/过场→opening，日常→other。"
        ),
    },
    {
        "id": "palworld",
        "name": "幻兽帕鲁",
        "window_regex": r"palworld|幻兽帕鲁",
        "sample_sec": 10,
        "event_prompt": (
            "你是一个《幻兽帕鲁》游戏观察员。根据画面描述识别游戏事件，"
            "只输出一个 JSON：{\"event_type\": \"kill|victory|death|drop|levelup|capture|other\", "
            "\"confidence\": 0~1, \"summary\": \"8-20字中文描述\"}。"
            "击败帕鲁→kill，捕获新帕鲁→capture，打败头目→victory，"
            "角色死亡→death，掉落→drop，升级→levelup，日常→other。"
        ),
    },
    {
        "id": "sekiro",
        "name": "只狼",
        "window_regex": r"sekiro|只狼",
        "sample_sec": 10,
        "event_prompt": (
            "你是一个《只狼》游戏观察员。根据画面描述识别游戏事件，"
            "只输出一个 JSON：{\"event_type\": \"kill|victory|death|drop|levelup|boss|other\", "
            "\"confidence\": 0~1, \"summary\": \"8-20字中文描述\"}。"
            "击杀敌人→kill，击败BOSS/精英→boss，通关→victory，"
            "角色死亡→death，获得道具→drop，升级→levelup，日常→other。"
        ),
    },
    {
        "id": "factorio",
        "name": "异星工厂",
        "window_regex": r"factorio|异星工厂",
        "sample_sec": 10,
        "event_prompt": (
            "你是一个《异星工厂》游戏观察员。根据画面描述识别游戏事件，"
            "只输出一个 JSON：{\"event_type\": \"kill|victory|death|drop|levelup|research|other\", "
            "\"confidence\": 0~1, \"summary\": \"8-20字中文描述\"}。"
            "清除虫巢→kill，科技研究完成→research，角色死亡→death，"
            "采集/掉落→drop，升级→levelup，日常→other。"
        ),
    },
]

# 高光事件（触发喝彩）。
HIGHLIGHT_EVENTS = {"kill", "victory", "boss", "capture", "levelup", "research"}


def get_game(game_id: str) -> Optional[dict]:
    return next((g for g in GAMES if g["id"] == game_id), None)


def match_game(title: str, app: str, window_regex: str = "") -> Optional[dict]:
    """按窗口标题/应用名匹配目标游戏；window_regex 优先（用户自定义）。"""
    hay = f"{title or ''} {app or ''}".lower()
    if window_regex:
        try:
            if re.search(window_regex, hay, re.IGNORECASE):
                # 用户自定义正则优先，返回第一个内置游戏（仅作事件提示词模板）
                return GAMES[0] if GAMES else None
        except re.error:
            return None
        return None
    for game in GAMES:
        try:
            if re.search(game["window_regex"], hay, re.IGNORECASE):
                return game
        except re.error:
            continue
    return None
