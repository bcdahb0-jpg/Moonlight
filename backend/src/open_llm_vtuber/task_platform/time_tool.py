"""Deterministic current-time result for chat tool calls.

The time query is deliberately resolved in-process.  A repeated public GET
request can be served from a stale proxy cache, and asking an LLM to rewrite a
timestamp can reintroduce an older value from its context.  The backend clock
is the same clock used by the server logs and is converted explicitly to the
requested timezone.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx


_TIME_GOAL_RE = re.compile(
    r"(?:现在几点|当前时间|当前准确时间|准确时间|当前系统时间|北京时间|现在的时间|几点了|什么时间)",
    re.IGNORECASE,
)
# Asia/Shanghai is UTC+8 year-round.  Use a fixed offset here so the feature
# also works in the packaged Windows runtime where the optional tzdata package
# may not be installed.
_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def is_current_time_goal(goal: str) -> bool:
    """Return whether a delegated goal asks for the current clock time."""
    return bool(_TIME_GOAL_RE.search(str(goal or "")))


def _result_for(now: datetime, source: str) -> dict[str, object]:
    weekday = "星期一星期二星期三星期四星期五星期六星期日".split("星期")[now.weekday() + 1]
    formatted = now.strftime("%Y-%m-%d %H:%M:%S")
    summary = (
        f"当前时间：{now:%Y}年{now.month}月{now.day}日（{weekday}）"
        f" {now:%H:%M:%S}；时区：Asia/Shanghai（UTC+8）。"
        f"原始来源：{source}；完整时间戳：{formatted}+08:00。"
    )
    return {
        "ok": True,
        "status": "completed",
        "summary": summary,
        "error": "",
        "direct": True,
        "source": source,
        "timestamp": now.isoformat(timespec="seconds"),
    }


def current_time_result() -> dict[str, object]:
    """Build a local-clock fallback result."""
    now = datetime.now(timezone.utc).astimezone(_SHANGHAI)
    return _result_for(now, "Moonlight 后端系统时钟（网络校时失败或结果陈旧）")


async def accurate_time_result() -> dict[str, object]:
    """Fetch current time with cache bypass, rejecting stale API responses."""
    local_now = datetime.now(timezone.utc).astimezone(_SHANGHAI)
    url = "https://timeapi.io/api/Time/current/zone"
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=True) as client:
            response = await client.get(
                url,
                params={"timeZone": "Asia/Shanghai", "_": time.time_ns()},
                headers={
                    "Cache-Control": "no-cache, no-store, max-age=0",
                    "Pragma": "no-cache",
                    "User-Agent": "Moonlight-time-check/1.0",
                },
            )
            response.raise_for_status()
            data = response.json()
            raw = str(data.get("dateTime") or data.get("currentLocalTime") or "")
            if not raw:
                raise ValueError("timeapi response has no dateTime")
            candidate = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=_SHANGHAI)
            candidate = candidate.astimezone(_SHANGHAI)
            date_header = response.headers.get("Date")
            if date_header:
                server_now = parsedate_to_datetime(date_header).astimezone(_SHANGHAI)
                if abs((candidate - server_now).total_seconds()) > 120:
                    # Some intermediaries cache the JSON body but still update
                    # the HTTP Date header. Prefer the response clock in that
                    # case instead of falling back to a known-skewed machine
                    # clock.
                    return _result_for(server_now, "timeapi.io HTTP Date（JSON 响应疑似缓存）")
            # A cached response was observed in production (~24 minutes old).
            # Never present an obviously stale remote value as current time.
            if abs((candidate - local_now).total_seconds()) > 300:
                raise ValueError("remote time response is stale")
            return _result_for(candidate, "timeapi.io（缓存穿透校验通过）")
    except Exception:
        return current_time_result()


__all__ = ["accurate_time_result", "current_time_result", "is_current_time_goal"]
