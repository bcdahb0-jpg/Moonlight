import abc
import asyncio


class TranslationError(Exception):
    """翻译失败（网络错误 / 上游限流 / 解析失败等）。

    由 translate_async 在无法产出有效译文时抛出，调用方据此降级：
    - 音频路径：跨语言翻译失败时跳过该句语音（避免把原文喂给外语 TTS 出杂音）
    - 字幕路径：回退原文（纯显示，无副作用）
    不要用返回原文来"软失败"——跨语言时原文进外语 TTS 必然出杂音。
    """


class TranslateInterface(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def translate(self, text: str) -> str:
        """
        Translate the input text to the target language."""
        raise NotImplementedError

    async def translate_async(self, text: str) -> str:
        """异步翻译：事件循环内调用不阻塞（await httpx.AsyncClient 等）。

        默认实现用 asyncio.to_thread 包一层同步 translate()，保证任何引擎在
        async 上下文里调用都不会卡住事件循环；已实现真异步的引擎（llm / deeplx）
        直接覆盖此方法。
        """
        return await asyncio.to_thread(self.translate, text)
