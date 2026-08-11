import re
import httpx
from loguru import logger
from .translate_interface import TranslateInterface, TranslationError


# ---------------------------------------------------------------------------
# LLM 翻译输出侧净化：LLM（DeepSeek）偶尔不遵守「只输出译文」规则，在译文前后
# 追加「提示用户提供内容」的引导语（实测：纯 emoji 输入 '😊' -> '請傳送要翻譯的
# 中文台詞...'；正常长文本译文后追加「请发送需要翻译的中文台词内容」）。这些垃圾
# 必须剥离，否则会以字幕/语音的形式原样呈现给用户。
# 命中即从该位置截断（提示语通常在结尾；若整段都是提示语，截断后为空 -> 回退原文）。
# ---------------------------------------------------------------------------

# 中文引导语：要求「发送/输入/提供/传」类动词或「需要翻译」短语出现，且附近（≤12字）
# 出现「翻译」关键词才命中，避免误伤正常译文。
_RE_NOISE_CN = re.compile(
    r"[请請]?\s*(发送|發送|输入|輸入|提供|傳|传|把|将|將)[^。！？!?\n]{0,12}?(翻译|翻譯)[^。！？!?\n]*"
)
_RE_NOISE_CN2 = re.compile(
    r"需要(翻译|翻譯)[^。！？!?\n]{0,12}?(台词|台詞|内容|內容|文本|文字)[^。！？!?\n]*"
)

# 英文引导语：要求「send/provide/give/enter/input」类动词 + 附近出现 translate 关键词
# （translate/translated/translation/translating 等变形用 translat\w* 覆盖）。
_RE_NOISE_EN = re.compile(
    r"(please|kindly)?\s*(send|provide|give me|enter|input)\s+(me\s+)?(the|your|this)?"
    r"[^.\n]{0,40}?translat\w*[^.\n]*",
    re.IGNORECASE,
)

_RE_NOISE_PATTERNS = [_RE_NOISE_CN, _RE_NOISE_CN2, _RE_NOISE_EN]

# 前缀废话包装：LLM 偶尔输出「翻译：xxx」「译文：xxx」「Translation: xxx」。
_RE_PREFIX_WRAP = re.compile(
    r"^\s*(翻译|翻譯|译文|譯文|翻译结果|翻譯結果|Translation|Translated text|Result)[:：]\s*",
    re.IGNORECASE,
)


def sanitize_translation(result: str) -> str:
    """剥离 LLM 翻译输出中的提示语垃圾与废话包装，返回干净译文。

    - 剥离「翻译：」类前缀包装；
    - 剥离成对引号包裹（prompt 已禁止，防御性处理）；
    - 从「提示语」模式命中处截断（提示语通常在译文结尾）；
    - 剥离后为空/纯标点 -> 返回空串，调用方应回退原文。
    """
    if not result:
        return ""
    text = result.strip()
    m = _RE_PREFIX_WRAP.search(text)
    if m:
        text = text[m.end():].strip()
    # 剥离成对引号包裹（prompt 已禁止，防御性处理）：
    # 英文引号成对（"" / ''），中文引号是「」『』成对而非对称。
    if len(text) >= 2:
        pair = (text[0], text[-1])
        if (pair in (("「", "」"), ("『", "』"), ('"', '"'), ("'", "'"))
                or (text[0] == text[-1] and text[0] in "\"'")):
            inner = text[1:-1].strip()
            if inner:
                text = inner
    cut = len(text)
    for pat in _RE_NOISE_PATTERNS:
        m = pat.search(text)
        if m:
            cut = min(cut, m.start())
    # 只清截断处的空白，不动译文自己的结尾标点/波浪号（「啦~」的 ~ 是正常口语）。
    text = text[:cut].strip()
    return text


class LLMTranslate(TranslateInterface):
    """Translate via an OpenAI-compatible LLM endpoint (e.g. Ollama, OpenAI, DeepSeek).

    Used to read subtitles in one language while TTS speaks another, without
    needing a dedicated translation service like DeepLX.
    """

    def __init__(
        self, api_endpoint: str, model: str, target_lang: str, api_key: str = ""
    ):
        self.api_endpoint = api_endpoint
        self.model = model
        self.target_lang = target_lang
        self.api_key = api_key or ""

    def _build_prompt(self, text: str) -> str:
        return (
            f"你是翻譯器。把下面的中文翻譯成自然、口語的{self.target_lang}。"
            f"這是 AI 角色的台詞，語氣自然口語。規則："
            f"1. 忽略圓括號、方括號或星號中的動作/表情/場景描寫（如（微笑）、[歎氣]、*歪頭*），只翻譯台詞正文；"
            f"2. 保持簡潔，與原文長度相當，不要擴寫、不要添加解釋或額外客套話；"
            f"3. 只輸出譯文本身，不要引號、羅馬拼音或任何附加文字；"
            f"4. 這是一次性翻譯請求，沒有後續對話；絕對不要輸出任何提示、提問或引導語"
            f"（例如「請發送要翻譯的內容」「請輸入需要翻譯的台詞」）。\n\n{text}"
        )

    def _payload(self, prompt: str) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "stream": False,
        }

    def _headers(self) -> dict | None:
        if self.api_key and self.api_key != "ollama":
            return {"Authorization": f"Bearer {self.api_key}"}
        return None

    @staticmethod
    def _clean_result(text: str, res: str) -> str:
        """输出侧净化 + 空值回退，返回最终可用的译文（失败时回原文）。"""
        if not res:
            logger.warning(
                f"LLM translate returned empty for '{text[:40]}', using original"
            )
            return text
        cleaned = sanitize_translation(res)
        if not cleaned:
            logger.warning(
                f"LLM translate returned only prompt-noise for '{text[:40]}', using original"
            )
            return text
        return cleaned

    def translate(self, text: str) -> str:
        try:
            resp = httpx.post(
                self.api_endpoint,
                json=self._payload(self._build_prompt(text)),
                headers=self._headers(),
                timeout=30,
            )
            res = resp.json()["choices"][0]["message"]["content"].strip()
            cleaned = self._clean_result(text, res)
            logger.info(f"LLM translate: '{text[:40]}' -> '{cleaned[:40]}'")
            return cleaned
        except Exception as e:
            logger.critical(f"LLM translate error '{text[:40]}'. Error: {e}")
            # fallback: 回原文，避免對話中斷
            return text

    async def translate_async(self, text: str) -> str:
        """异步版本：httpx.AsyncClient，事件循环内不阻塞。

        DeepSeek 等 API 翻译单次往返 5~10s，同步调用会卡死整个事件循环
        （其他 WebSocket 客户端/表情信号全部排队）——必须异步化。

        失败时抛 TranslationError，由调用方降级（音频跨语言失败跳过语音，
        字幕回退原文），不在引擎内静默回退原文——跨语言时会把原文喂给
        外语 TTS 出杂音。
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self.api_endpoint,
                    json=self._payload(self._build_prompt(text)),
                    headers=self._headers(),
                )
            res = resp.json()["choices"][0]["message"]["content"].strip()
            cleaned = self._clean_result(text, res)
            logger.info(f"LLM translate: '{text[:40]}' -> '{cleaned[:40]}'")
            return cleaned
        except Exception as e:
            logger.warning(f"LLM translate error '{text[:40]}'. Error: {e}")
            raise TranslationError(str(e)) from e
