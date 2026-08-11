"""LLM 接入层（plan §5.2）：从 conf_bridge 配置构建 ChatOpenAI。

单向依赖：llm_adapter.py ← graph.py / session.py；不 import models/sandbox/mcp。

配置缺省 → 启动即抛 `TaskLLMConfigError`（fail fast，plan §5.6 缺省可配但缺 key 应明确报错）。

`context_window`（v3 Phase 6 token 预算用）：按模型名探测上下文窗口（token 数）。
- `cfg.llm_context_window > 0` → 显式配置优先（conf.yaml 覆盖）。
- 否则查已知模型映射表（DeepSeek 1M / 常见模型 128k），未知模型回退 64k。
- 探测是纯静态映射（不查 API），DeepSeek 1M 上下文已实测量级正确。
"""

from __future__ import annotations

from typing import Optional

from langchain_openai import ChatOpenAI

from .conf_bridge import TaskPlatformConfig, task_config


class TaskLLMConfigError(RuntimeError):
    """task_platform LLM 配置缺失/非法。"""


#: 已知模型 → 上下文窗口（token）。DeepSeek 官方 1M（deepseek-chat/deepseek-reasoner
#: 2025-12 起 128K→1M；deepseek-v4-flash/-pro 2026-04 起原生 1M）；其余按主流默认。
#: cfg.llm_context_window>0 可显式覆盖。
_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-chat": 1_000_000,
    "deepseek-reasoner": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "claude-3-5-sonnet": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-sonnet-4": 1_000_000,
    "qwen-max": 32_000,
    "qwen-plus": 131_072,
    "glm-4": 128_000,
}
_DEFAULT_CONTEXT_WINDOW = 64_000  # 未知模型保守回退


def context_window(cfg: Optional[TaskPlatformConfig] = None) -> int:
    """任务主模型的上下文窗口（token 数）——token 预算/压缩比例触发用。"""
    cfg = cfg or task_config()
    if cfg.llm_context_window and cfg.llm_context_window > 0:
        return cfg.llm_context_window
    for key, size in _KNOWN_CONTEXT_WINDOWS.items():
        if key in (cfg.llm_model or ""):
            return size
    return _DEFAULT_CONTEXT_WINDOW


def build_chat_model(
    cfg: Optional[TaskPlatformConfig] = None,
) -> ChatOpenAI:
    """根据 task_platform 配置构建 ChatOpenAI 实例。

    从 conf.yaml `character_config.agent_config.llm_configs.openai_compatible_llm`
    读取 base_url / api_key / model（conf_bridge 已解析）。

    Raises:
        TaskLLMConfigError: base_url/api_key/model 三者任一缺失。
    """
    cfg = cfg or task_config()
    if not (cfg.llm_base_url and cfg.llm_api_key and cfg.llm_model):
        raise TaskLLMConfigError(
            "task_platform 未配置 LLM：conf.yaml 的 openai_compatible_llm 块需含 "
            "base_url / llm_api_key / model 三个字段（task_platform.llm_* 快照）。"
        )
    return ChatOpenAI(
        base_url=cfg.llm_base_url,
        api_key=cfg.llm_api_key,
        model=cfg.llm_model,
        temperature=0.3,  # 工程任务低熵优先（plan §5.2 G7 内核 prompt 风格）
        streaming=True,
    )
