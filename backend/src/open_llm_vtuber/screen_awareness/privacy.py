"""隐私规则（后端二次校验）。

前端采集前已做一次阻断（privacyRules.ts）；后端在收到帧时**再次校验**，
确保「敏感规则命中时 0 张图像离开本机」的承诺由后端兜底：
- Moonlight 自身窗口（标题/应用特征）；
- 密码管理器、登录/支付、系统凭据等已知敏感应用；
- 用户配置的应用黑名单 + 标题关键词 + 仅允许列表。
"""
from __future__ import annotations

import re
from typing import Optional

from .models import ScreenConfig, ScreenWindowInfo

# Moonlight 自身窗口特征（Electron BrowserWindow title / app）。
MOONLIGHT_TITLE_PATTERNS = ("moonlight", "月光", "小月")
MOONLIGHT_APP_NAMES = ("moonlight",)

# 已知敏感应用（进程名，小写比较）。
SENSITIVE_APPS = (
    "1password",
    "bitwarden",
    "keepass",
    "lastpass",
    "kaspersky",
    "norton",
    "windowssecurity",
    "credentialmanager",
    "password",
    "paypal",
    "alipay",
    "wechatpay",
    "unionpay",
    "网银",
    "银行",
)

# 标题关键词（小写比较，命中即阻断）。
SENSITIVE_TITLE_KEYWORDS = (
    "password",
    "passwort",
    "parol",
    "senha",
    "contraseña",
    "密碼",
    "密码",
    "口令",
    "登录",
    "登陆",
    "sign in",
    "signin",
    "log in",
    "login",
    "支付",
    "付款",
    "结账",
    "checkout",
    "银行卡",
    "验证码",
    "otp",
    "2fa",
    "two-factor",
    "credential",
    "secret",
    "私密浏览",
    "incognito",
)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


class PrivacyRule:
    """单条规则的匹配器（编译后复用，避免每帧重复编译正则）。"""

    def __init__(self, pattern: str) -> None:
        self.raw = pattern
        try:
            self._re = re.compile(re.escape(pattern), re.IGNORECASE)
        except Exception:  # pragma: no cover - 极端输入兜底
            self._re = None

    def matches(self, text: str) -> bool:
        if self._re is None:
            return False
        return bool(self._re.search(text or ""))


class PrivacyPolicy:
    """基于配置构建的隐私判定器（后端二次校验）。"""

    def __init__(self, config: ScreenConfig) -> None:
        self._reload(config)

    def _reload(self, config: ScreenConfig) -> None:
        self._app_blocklist = tuple(_norm(a) for a in config.blocked_apps if a)
        self._title_keywords = tuple(
            _norm(k) for k in config.blocked_title_keywords if k
        )
        self._allowlist = tuple(_norm(a) for a in config.allowlist_apps if a)
        self._custom_title_rules = [
            PrivacyRule(k) for k in config.blocked_title_keywords if k
        ]

    def reload(self, config: ScreenConfig) -> None:
        self._reload(config)

    def check(self, window: ScreenWindowInfo) -> tuple[bool, str]:
        """返回 (blocked, reason)。blocked=True 时该帧不得上传/分析。"""
        title = _norm(window.title)
        app = _norm(window.app)

        # 仅允许列表：命中则跳过其余检查（用户显式信任）。
        if self._allowlist:
            if app and app in self._allowlist:
                return False, ""
            if title and any(a in title for a in self._allowlist):
                return False, ""

        # Moonlight 自身。
        for pat in MOONLIGHT_TITLE_PATTERNS:
            if pat in title:
                return True, "moonlight_self"
        if app in MOONLIGHT_APP_NAMES:
            return True, "moonlight_self"

        # 已知敏感应用。
        if app:
            for s in SENSITIVE_APPS:
                if s in app:
                    return True, "sensitive_app"
        # 标题关键词。
        for kw in SENSITIVE_TITLE_KEYWORDS:
            if kw in title:
                return True, "title_keyword"
        # 用户自定义。
        if app in self._app_blocklist:
            return True, "user_blocklist_app"
        for rule in self._custom_title_rules:
            if rule.matches(window.title):
                return True, "user_blocklist_title"
        if self._title_keywords:
            for kw in self._title_keywords:
                if kw in title:
                    return True, "user_blocklist_title"

        return False, ""


def blocked_reason(config: ScreenConfig, window: ScreenWindowInfo) -> Optional[str]:
    """便捷函数：被阻断返回原因字符串，否则 None。"""
    blocked, reason = PrivacyPolicy(config).check(window)
    return reason if blocked else None
