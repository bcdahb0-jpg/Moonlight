"""视觉模型调用与严格结构化输出（fail-soft）。

- 通过 OpenAI 兼容 ``POST {base_url}/chat/completions`` 调用视觉模型；
- 请求 ``response_format={"type": "json_object"}``（不支持时服务端忽略），
  并让模型输出计划 §5 的 ScreenSnapshot JSON；解析失败时尝试从文本中提取
  JSON 块，仍失败则返回 ``None``（保留窗口元数据上下文，不阻塞聊天）；
- provider 缺省/超时/429/断网一律返回 None，绝不抛出；
- ``local_only=True`` 时拒绝非 localhost 端点（仅本地模型模式）。
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Optional

import httpx
from loguru import logger

from .models import ScreenConfig, ScreenFrame, ScreenSnapshot
from .metrics import get_metrics

SYSTEM_PROMPT = (
    "你是一个桌面屏幕理解助手。用户会给你一张前台窗口的截图。"
    "请分析画面内容，并**只输出一个 JSON 对象**，字段如下：\n"
    '{"scene": "coding|reading|video|game|chat|design|unknown", '
    '"summary": "用简体中文一句话概括用户正在做什么（30字内）", '
    '"salient_text": ["屏幕上值得注意的短文本，如报错码/标题，最多3条，每条不超过20字符"], '
    '"possible_topic": "当前可能的聊天话题，简体中文，10字内", '
    '"sensitive": false, "worth_interrupting": false, "confidence": 0.0}\n'
    "规则：confidence 是 0-1 的浮点数；sensitive 仅在画面出现密码、支付、登录凭据时置 true；"
    "worth_interrupting 仅在出现报错/异常/值得主动提醒的显著变化时置 true。"
    "不要输出 JSON 以外的任何文字。"
)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class VisionAnalyzer:
    """视觉分析器。同一实例可复用（线程安全，无共享可变状态）。"""

    def __init__(self, config: ScreenConfig) -> None:
        self.config = config
        self._timeout = httpx.Timeout(30.0, connect=10.0)

    def reload(self, config: ScreenConfig) -> None:
        self.config = config

    @property
    def provider_label(self) -> str:
        cfg = self.config
        if not (cfg.base_url or cfg.model):
            return "inherit"
        host = (cfg.base_url or "").split("//")[-1].split("/")[0]
        return f"{cfg.provider}@{host or 'inherit'}"

    def _endpoint(self) -> Optional[str]:
        """返回 chat/completions URL；无 provider 或违反 local_only 时返回 None。"""
        cfg = self.config
        base = (cfg.base_url or "").strip().rstrip("/")
        if not base:
            return None
        if cfg.local_only:
            ok = base.startswith(("http://localhost", "http://127.0.0.1", "http://0.0.0.0"))
            if not ok:
                logger.warning(
                    "[screen_awareness] local_only mode rejects remote vision endpoint"
                )
                return None
        return base + "/chat/completions"

    async def analyze(
        self, frame: ScreenFrame, api_key: str = "", timeout_sec: float = 25.0
    ) -> Optional[ScreenSnapshot]:
        """分析一帧。任何失败返回 None（fail-soft）。

        Args:
            frame: 已通过隐私校验的帧（image 为 data URL）。
            api_key: 视觉 provider 的 API key（空则请求不带 Authorization）。
            timeout_sec: 单次分析超时。

        Returns:
            结构化摘要；无 provider / 解析失败 / 网络错误时 None。
        """
        if not frame.image:
            return None
        endpoint = self._endpoint()
        if endpoint is None:
            get_metrics().inc("frames_dropped")
            return None

        image_url = _data_url_to_api_url(frame.image)
        if image_url is None:
            return None

        model = self.config.model or "gpt-4o"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _describe_window(frame)},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "low"},
                        },
                    ],
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
            elapsed = (time.monotonic() - t0) * 1000.0
            get_metrics().observe("analyze", elapsed)
            if resp.status_code == 429:
                logger.warning("[screen_awareness] vision 429 rate limited")
                get_metrics().inc("analyze_errors")
                return None
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage") or {}
            tok = int(usage.get("total_tokens") or 0)
            in_tok = int(usage.get("prompt_tokens") or 0)
            out_tok = int(usage.get("completion_tokens") or 0)
            if tok:
                get_metrics().inc("vision_tokens", tok)
            if in_tok:
                get_metrics().inc("vision_input_tokens", in_tok)
            if out_tok:
                get_metrics().inc("vision_output_tokens", out_tok)
            snap = _parse_snapshot(content)
            if snap is None:
                get_metrics().inc("analyze_errors")
                logger.warning("[screen_awareness] vision output parse failed")
            else:
                get_metrics().inc("analyze_count")
            return snap
        except Exception as e:
            get_metrics().inc("analyze_errors")
            logger.debug(f"[screen_awareness] analyze failed: {type(e).__name__}: {e}")
            return None


def _describe_window(frame: ScreenFrame) -> str:
    w = frame.window
    parts = [f"前台窗口：{w.app or 'unknown'}"]
    if w.title:
        parts.append(f"标题：{w.title[:80]}")
    parts.append(f"截图原因：{frame.reason}")
    return "；".join(parts)


def _data_url_to_api_url(data_url: str) -> Optional[str]:
    """data:image/webp;base64,xxx -> data:image/webp;base64,xxx（保留 mime 头）。"""
    if not data_url or not data_url.startswith("data:image/"):
        return None
    try:
        # 校验 base64 部分合法（不真正解码，避免大内存拷贝）。
        head, _, b64 = data_url.partition(",")
        if not b64 or not head.endswith(";base64"):
            return None
        base64.b64decode(b64[:64], validate=False)
    except Exception:
        return None
    return data_url


def _parse_snapshot(content: str) -> Optional[ScreenSnapshot]:
    """从模型输出解析 ScreenSnapshot；容忍多余文字 / 代码围栏。"""
    if not content:
        return None
    text = content.strip()
    # 去掉 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    payload: dict | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if m:
            try:
                payload = json.loads(m.group(0))
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return None
    try:
        snap = ScreenSnapshot.model_validate(payload)
    except Exception:
        # 字段缺失/类型错误：尽力用默认值兜底构造。
        try:
            snap = ScreenSnapshot(
                scene=str(payload.get("scene") or "unknown"),
                summary=str(payload.get("summary") or "")[:120],
                salient_text=[
                    str(s)[:40] for s in (payload.get("salient_text") or [])
                ][:3],
                possible_topic=str(payload.get("possible_topic") or "")[:20],
                sensitive=bool(payload.get("sensitive", False)),
                worth_interrupting=bool(payload.get("worth_interrupting", False)),
                confidence=float(payload.get("confidence") or 0.0),
            )
        except Exception:
            return None
    return snap
