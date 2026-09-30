"""模型配置定义与查询。

提供内置模型配置与查询接口：上下文窗口与输出上限来自内置表或
用户自定义供应商配置，均未命中时按 200k 回退并显式告警——
窗口回退不再静默发生，容量面板可据 window_source 标注来源。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelConfig dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelConfig:
    """模型配置。"""

    name: str
    context_window: int  # 上下文窗口大小（token）
    max_output_tokens: int  # 最大输出 token
    supports_streaming: bool = True  # 是否支持流式
    supports_tools: bool = True  # 是否支持工具调用
    supports_vision: bool = False  # 是否支持视觉
    window_source: str = "fallback"  # 窗口来源：internal/provider/fallback


# ---------------------------------------------------------------------------
# 内置模型配置
# ---------------------------------------------------------------------------

_BUILTIN_MODELS: dict[str, ModelConfig] = {
    "gpt-4o": ModelConfig("gpt-4o", 128000, 16384, supports_vision=True),
    "gpt-4o-mini": ModelConfig("gpt-4o-mini", 128000, 16384, supports_vision=True),
    "o3-mini": ModelConfig("o3-mini", 200000, 100000),
    "claude-3-5-sonnet": ModelConfig("claude-3-5-sonnet", 200000, 8192, supports_vision=True),
    "claude-3-7-sonnet": ModelConfig("claude-3-7-sonnet", 200000, 64000, supports_vision=True),
    "deepseek-chat": ModelConfig("deepseek-chat", 65536, 8192),
    "deepseek-reasoner": ModelConfig("deepseek-reasoner", 65536, 8192),
}

# 默认模型配置（查不到任何表时的回退）
_DEFAULT_MODEL_CONFIG = ModelConfig(
    name="default",
    context_window=200000,
    max_output_tokens=32768,
    supports_streaming=True,
    supports_tools=True,
    supports_vision=False,
)

# 模型名尾部的日期/版本后缀（如 -20260929、-latest、-preview、-rc1），归一化时剥离
_VERSION_SUFFIX_RE = re.compile(
    r"(?:-\d{4,10}|-(?:latest|preview|exp|beta|rc\d*|thinking))$", re.IGNORECASE
)

# 回退告警去重：同一模型名只告警一次
_warned_fallbacks: set[str] = set()


def _normalize_model_name(name: str) -> str:
    """归一化模型名：小写并反复剥离尾部日期/版本后缀。"""
    n = name.strip().lower()
    while True:
        stripped = _VERSION_SUFFIX_RE.sub("", n)
        if stripped == n:
            return n
        n = stripped


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------


def get_model_config(model: str) -> ModelConfig:
    """根据模型名返回配置。

    查找策略（内置表与自定义供应商均先原始名精确、再归一化匹配）：
      1. 内置表精确匹配
      2. 内置表归一化精确/前缀匹配
      3. 用户配置的自定义供应商查 context_window / max_output_tokens
      4. 均未命中返回默认配置（200k 回退），并按模型名告警一次

    Args:
        model: 模型名称，如 "gpt-4o"、"deepseek-chat-v3"

    Returns:
        对应的 ModelConfig（window_source 标注窗口来源）
    """
    if not model:
        return _DEFAULT_MODEL_CONFIG

    normalized = _normalize_model_name(model)

    # 1. 内置表精确匹配
    if model in _BUILTIN_MODELS:
        return replace(_BUILTIN_MODELS[model], window_source="internal")

    # 2. 内置表归一化精确 / 前缀匹配（如 "gpt-4o-2026-05-13" 归一后匹配 "gpt-4o"）
    if normalized in _BUILTIN_MODELS:
        return replace(_BUILTIN_MODELS[normalized], window_source="internal")
    for key, config in _BUILTIN_MODELS.items():
        if normalized.startswith(key.lower() + "-"):
            return replace(config, window_source="internal")

    # 3. 从用户配置的自定义供应商里查窗口与输出上限
    try:
        from startup.config import get_global_config
        gc = get_global_config()
        for provider in gc.llm_providers:
            for m in provider.get("models", []):
                mid = m.get("model_id", "")
                if mid and _normalize_model_name(mid) == normalized:
                    if "context_window" not in m:
                        logger.warning(
                            "供应商模型 %s 未配置 context_window，按 200000 处理", model
                        )
                    return ModelConfig(
                        name=model,
                        context_window=m.get("context_window", 200000),
                        max_output_tokens=m.get("max_output_tokens", 32768),
                        window_source="provider",
                    )
    except Exception:
        logger.debug("自定义供应商配置读取失败，按内置查询结果处理", exc_info=True)

    # 4. 回退默认窗口：显式告警，不再静默
    _warn_fallback(model)
    return _DEFAULT_MODEL_CONFIG


def get_model_window_info(model: str) -> tuple[int, int, str]:
    """获取模型窗口信息三元组：(context_window, max_output_tokens, window_source)。

    压缩阈值计算与容量面板统一从这里取值，保证触发判定与展示同源。
    """
    config = get_model_config(model)
    return config.context_window, config.max_output_tokens, config.window_source


def get_effective_context_window(model: str) -> int:
    """获取有效上下文窗口大小。

    Args:
        model: 模型名称

    Returns:
        上下文窗口大小（token 数）
    """
    config = get_model_config(model)
    return config.context_window


def _warn_fallback(model: str) -> None:
    """窗口回退告警（同一模型名只告警一次，避免循环刷屏）。"""
    if model in _warned_fallbacks:
        return
    _warned_fallbacks.add(model)
    logger.warning(
        "模型 %s 未在内置表或供应商配置中命中窗口定义，回退 200000 token 窗口"
        "（压缩阈值将按回退窗口计算）",
        model,
    )
