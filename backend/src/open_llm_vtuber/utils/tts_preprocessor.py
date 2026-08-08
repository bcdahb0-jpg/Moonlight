import re
import unicodedata
from loguru import logger
from ..translate.translate_interface import TranslateInterface


def tts_filter(
    text: str,
    remove_special_char: bool,
    ignore_brackets: bool,
    ignore_parentheses: bool,
    ignore_asterisks: bool,
    ignore_angle_brackets: bool,
    translator: TranslateInterface | None = None,
) -> str:
    """
    Filter or do anything to the text before TTS generates the audio.
    Changes here do not affect subtitles or LLM's memory. The generated audio is
    the only affected thing.

    Args:
        text (str): The text to filter.
        remove_special_char (bool): Whether to remove special characters.
        ignore_brackets (bool): Whether to ignore text within brackets.
        ignore_parentheses (bool): Whether to ignore text within parentheses.
        ignore_asterisks (bool): Whether to ignore text within asterisks.
        translator (TranslateInterface, optional):
            The translator to use. If None, we'll skip the translation. Defaults to None.

    Returns:
        str: The filtered text.
    """
    # NFKC 归一化：把全角（）、［］、＜＞、＊ 等转成半角，让下面的半角过滤器能剥离
    # 动作/表情描述（如 AI 用全角（微笑）），否则这些内容会被 TTS 读出来、和聊天框不一致。
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception as e:
        logger.warning(f"NFKC normalize failed: {e}")

    # 剥离 Markdown 残留（代码块/反引号、URL、列表项、标题符号），避免 TTS 把
    # `1.` `-` `#` `https://` 等读成"一 点 / 杠 / 井号"之类的怪声。
    # 必须在任何压缩空白的步骤之前做：列表项/标题需要按"行"识别。
    try:
        text = strip_markdown_residue(text)
    except Exception as e:
        logger.warning(f"Error stripping markdown residue: {e}")
        logger.warning(f"Text: {text}")
        logger.warning("Skipping...")

    if ignore_asterisks:
        try:
            text = filter_asterisks(text)
        except Exception as e:
            logger.warning(f"Error ignoring asterisks: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")

    if ignore_brackets:
        try:
            text = filter_brackets(text)
        except Exception as e:
            logger.warning(f"Error ignoring brackets: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    if ignore_parentheses:
        try:
            text = filter_parentheses(text)
        except Exception as e:
            logger.warning(f"Error ignoring parentheses: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    if ignore_angle_brackets:
        try:
            text = filter_angle_brackets(text)
        except Exception as e:
            logger.warning(f"Error ignoring angle brackets: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")
    if remove_special_char:
        try:
            text = remove_special_characters(text)
        except Exception as e:
            logger.warning(f"Error removing special characters: {e}")
            logger.warning(f"Text: {text}")
            logger.warning("Skipping...")

    # NFKC 会把中文全角标点（，。？！……）转成半角（,.?!...），中文 TTS 读半角
    # 标点会产生怪声/杂音（尤其是连续的 ... 省略号）。剥离动作描述后，若文本
    # 以中文为主，把标点恢复成全角，让语音朗读自然；英文文本保持半角不强制转。
    try:
        text = restore_cjk_punctuation(text)
    except Exception as e:
        logger.warning(f"Error restoring CJK punctuation: {e}")
        logger.warning(f"Text: {text}")
        logger.warning("Skipping...")

    if translator:
        try:
            logger.info("Translating...")
            text = translator.translate(text)
            logger.info(f"Translated: {text}")
        except Exception as e:
            logger.critical(f"Error translating: {e}")
            logger.critical(f"Text: {text}")
            logger.warning("Skipping...")

    logger.debug(f"Filtered text: {text}")
    return text


def remove_special_characters(text: str) -> str:
    """
    Filter text to remove all non-letter, non-number, and non-punctuation characters.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    normalized_text = unicodedata.normalize("NFKC", text)

    def is_valid_char(char: str) -> bool:
        category = unicodedata.category(char)
        return (
            category.startswith("L")
            or category.startswith("N")
            or category.startswith("P")
            or char.isspace()
        )

    filtered_text = "".join(char for char in normalized_text if is_valid_char(char))
    return filtered_text


def strip_markdown_residue(text: str) -> str:
    """
    Strip Markdown residue that would otherwise be read aloud as noise:
    - code blocks / inline code (```...``` and `...`)
    - URLs (http://, https://, www.)
    - list items / headings on their own line ( - / * / + / 1. / # )
    - leftover bare markdown symbols (#, `, ~)

    Runs after the bracket/asterisk filters; only affects the spoken audio.
    """
    if not text:
        return text

    # 1) 代码块（多行反引号）与行内代码（单个反引号）
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)

    # 2) URL
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)

    # 3) 独立成行的列表项 / 标题 / 引用：整行跳过（列表项通常不适合朗读）
    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[-*+]\s+\S", stripped):  # - / * / + 列表项
            continue
        if re.match(r"^\d+[.、)）]\s*\S", stripped):  # 1. 1、 1) 列表项
            continue
        if re.match(r"^#{1,6}\s+\S", stripped):  # 标题
            continue
        if stripped.startswith(">"):  # 引用块
            continue
        kept_lines.append(stripped)
    text = " ".join(kept_lines)

    # 4) 残留的裸符号
    text = text.replace("#", " ").replace("`", " ").replace("~", " ")

    return re.sub(r"\s+", " ", text).strip()


def _filter_nested(text: str, left: str, right: str) -> str:
    """
    Generic function to handle nested symbols.

    Args:
        text (str): The text to filter.
        left (str): The left symbol (e.g. '[' or '(').
        right (str): The right symbol (e.g. ']' or ')').

    Returns:
        str: The filtered text.
    """
    if not isinstance(text, str):
        raise TypeError("Input must be a string")
    if not text:
        return text

    result = []
    depth = 0
    for char in text:
        if char == left:
            depth += 1
        elif char == right:
            if depth > 0:
                depth -= 1
        else:
            if depth == 0:
                result.append(char)
    filtered_text = "".join(result)
    filtered_text = re.sub(r"\s+", " ", filtered_text).strip()
    return filtered_text


def filter_brackets(text: str) -> str:
    """
    Filter text to remove all text within brackets, handling nested cases.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    return _filter_nested(text, "[", "]")


def filter_parentheses(text: str) -> str:
    """
    Filter text to remove all text within parentheses, handling nested cases.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    return _filter_nested(text, "(", ")")


def filter_angle_brackets(text: str) -> str:
    """
    Filter text to remove all text within angle brackets, handling nested cases.

    Args:
        text (str): The text to filter.

    Returns:
        str: The filtered text.
    """
    return _filter_nested(text, "<", ">")


def filter_asterisks(text: str) -> str:
    """
    Removes text enclosed within asterisks of any length (*, **, ***, etc.) from a string.

    Args:
        text: The input string.

    Returns:
        The string with asterisk-enclosed text removed.
    """
    # Handle asterisks of any length (*, **, ***, etc.)
    filtered_text = re.sub(r"\*{1,}((?!\*).)*?\*{1,}", "", text)

    # Clean up any extra spaces
    filtered_text = re.sub(r"\s+", " ", filtered_text).strip()

    return filtered_text


# 判断文本是否「以中文为主」：只要出现 CJK 统一表意文字（含扩展区）就算中文语境。
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def _is_cjk_text(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def restore_cjk_punctuation(text: str) -> str:
    """
    NFKC 归一化会把中文全角标点转成半角（，。？！…… → ,.? ! ...），中文 TTS 引擎
    （edge_tts / siliconflow / cosyvoice 等）读半角标点会产生怪声、拖音或「读符号」
    的杂音。本函数在剥离动作描述之后，把中文语境下的标点恢复成全角：
    - 逗号/句号/问号/感叹号/冒号/分号 → 全角
    - 连续 2+ 个半角点（...）→ 中文省略号（……）
    - 中括号/花括号残留 → 清理（NFKC 后半角 [ ] { } 在中文里是噪音）
    英文文本（无 CJK 字符）保持半角不强制转换，避免破坏英文标点。

    Args:
        text (str): 剥离动作描述后的 TTS 文本。

    Returns:
        str: 恢复全角标点后的文本。
    """
    if not text or not _is_cjk_text(text):
        return text

    # 1) 省略号：2+ 个连续半角点 → 中文省略号（……）。注意要在句点恢复前做，
    #    否则会把英文句号也卷进来；此处仅针对「连续点」。
    text = re.sub(r"\.{2,}", "……", text)
    # 2) 单个残留点：仅当两侧都不是英文字母/数字时才视为中文句号（避免误伤 v2.0 / 3.14）
    text = re.sub(r"(?<![A-Za-z0-9])\.(?![A-Za-z0-9])", "。", text)

    # 3) 常用半角标点 → 全角；冒号/分号同样避开英文数字邻接（避免误伤 http:// 之类）
    pairs = {
        ",": "，",
        "?": "？",
        "!": "！",
    }
    for half, full in pairs.items():
        text = text.replace(half, full)
    text = re.sub(r"(?<![A-Za-z0-9]):(?![A-Za-z0-9])", "：", text)
    text = re.sub(r"(?<![A-Za-z0-9]);(?![A-Za-z0-9])", "；", text)

    # 4) 中括号 / 花括号残留：NFKC 后半角 [ ] { } 在中文朗读里是噪音，直接清掉
    #    （内容已被 ignore_brackets 剥离过，这里只是兜底清理残留的单个括号）。
    text = text.replace("[", "").replace("]", "")
    text = text.replace("{", "").replace("}", "")

    # 5) 清理 NFKC 后半角波浪号 ~ 的残留（全角 ～ 被 NFKC 转成 ~，中文读它很怪）
    text = text.replace("~", "")

    # 6) 收尾：压缩多余空格
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_action_notes(text: str) -> str:
    """剥离 AI 台词中的动作/表情/场景描写，返回「纯台词」。

    用于**字幕翻译 / 音频翻译**的输入预处理：LLM 常输出形如
    ``哥哥晚上好呀～（微笑）今天过得怎么样？`` 的文本，若直接交给翻译器，
    动作描写会被翻成 ``(smiling)`` 之类冗长内容，导致字幕/语音比中文气泡
    长得多。本函数剥离圆括号/方括号/尖括号/星号包住的内容（兼容全角），
    并把连续空白压缩成单个空格。

    注意：与 tts_filter 不同，这里**不**恢复中文标点（翻译输入无需朗读级
    标点），也不做 remove_special_char（避免把台词里的 ~ 等语气符号删掉）。

    Args:
        text (str): LLM 原始输出文本。

    Returns:
        str: 剥离动作描写后的纯台词文本。
    """
    if not text:
        return text
    try:
        # NFKC：全角（）、［］、＜＞、＊ → 半角，让下面的剥离器能识别
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass
    text = filter_asterisks(text)      # *动作* / **强调**
    text = filter_brackets(text)       # [情绪标签]
    text = filter_parentheses(text)    # （动作/表情/场景）
    text = filter_angle_brackets(text)  # <动作>
    text = re.sub(r"\s+", " ", text).strip()
    return text
