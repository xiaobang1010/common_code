"""OpenAI 兼容 LLM 客户端模块。

提供线程安全的单例客户端实例、自定义 HTTP 客户端构建、
以及配置优先级解析。

配置优先级（从高到低）：
  - base_url: 自定义供应商 > 插件供应商 > LLM_BASE_URL 环境变量 > 配置文件 > 默认值
  - api_key:  自定义供应商 > 插件供应商 > LLM_API_KEY 环境变量 > 配置文件 > 默认值
  - model:    自定义供应商 > 插件供应商 > LLM_MODEL 环境变量 > 配置文件 > 默认值
  - api_format: 自定义供应商 > 插件供应商 > 默认值 "openai"
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

import httpx
import openai

from startup.config import get_global_config
from startup.config.constants import (
    DEFAULT_LLM_API_KEY,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MODEL,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CLIENT_REQUEST_ID_HEADER = "x-client-request-id"

# 默认模型（当配置文件和环境变量均未指定时使用）
_FALLBACK_MODEL = "Qwen/Qwen3-235B-A22B"

# ---------------------------------------------------------------------------
# 单例状态（线程安全）
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client_instance: openai.OpenAI | None = None
_default_model_cache: str | None = None


# ---------------------------------------------------------------------------
# get_custom_headers — 解析 CC_CUSTOM_HEADERS 环境变量
# ---------------------------------------------------------------------------


def get_custom_headers() -> dict[str, str]:
    """解析 CC_CUSTOM_HEADERS 环境变量。

    格式：key1:value1,key2:value2
    冒号分隔键值，逗号分隔多对。
    """
    custom_headers: dict[str, str] = {}
    raw = os.environ.get("CC_CUSTOM_HEADERS", "")
    if not raw:
        return custom_headers

    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        colon_idx = pair.find(":")
        if colon_idx == -1:
            continue
        name = pair[:colon_idx].strip()
        value = pair[colon_idx + 1 :].strip()
        if name:
            custom_headers[name] = value

    return custom_headers


# ---------------------------------------------------------------------------
# build_http_client — 构建自定义 HTTP 客户端
# ---------------------------------------------------------------------------


def build_http_client() -> httpx.Client:
    """构建自定义 httpx.Client。

    功能：
      - 注入 x-client-request-id header（UUID）
      - 注入 CC_CUSTOM_HEADERS 中的自定义 header
      - 支持代理配置（HTTPS_PROXY / https_proxy / HTTP_PROXY / http_proxy）
    """
    headers: dict[str, str] = {
        CLIENT_REQUEST_ID_HEADER: str(uuid.uuid4()),
    }
    headers.update(get_custom_headers())

    # 代理配置
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )

    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": httpx.Timeout(600.0, connect=10.0),
    }
    if proxy:
        kwargs["proxy"] = proxy

    return httpx.Client(**kwargs)


# ---------------------------------------------------------------------------
# 配置解析辅助
# ---------------------------------------------------------------------------


def _resolve_base_url() -> str:
    """解析 base_url。

    优先级：自定义供应商 > 插件供应商 > LLM_BASE_URL 环境变量 > 配置文件 > 默认值。
    """
    # 优先从 LLM 供应商注册表取（注册表内部自定义优先于插件）
    try:
        from query.services.api.providers import get_registry
        provider = get_registry().get_active_provider()
        if provider is not None and provider.get("base_url"):
            return provider["base_url"]
    except ImportError:
        pass

    return (
        os.environ.get(ENV_LLM_BASE_URL)
        or _get_config_field("llm_base_url")
        or DEFAULT_LLM_BASE_URL
    )


def _resolve_api_key() -> str | None:
    """解析 api_key。

    优先级：自定义供应商 > 插件供应商 > LLM_API_KEY 环境变量 > 配置文件 > 默认值。
    """
    # 优先从 LLM 供应商注册表取
    try:
        from query.services.api.providers import get_registry
        provider = get_registry().get_active_provider()
        if provider is not None and provider.get("api_key"):
            return provider["api_key"]
    except ImportError:
        pass

    return (
        os.environ.get(ENV_LLM_API_KEY)
        or _get_config_field("llm_api_key")
        or DEFAULT_LLM_API_KEY
    )


def _resolve_model() -> str:
    """解析默认模型名。

    优先级：自定义供应商 > 插件供应商 > LLM_MODEL 环境变量 > 配置文件 > 默认值。
    """
    # 优先从 LLM 供应商注册表取
    try:
        from query.services.api.providers import get_registry
        provider = get_registry().get_active_provider()
        if provider is not None and provider.get("model"):
            return provider["model"]
    except ImportError:
        pass

    return (
        os.environ.get(ENV_LLM_MODEL)
        or _get_config_field("llm_model")
        or DEFAULT_LLM_MODEL
        or _FALLBACK_MODEL
    )


def _resolve_api_format() -> str:
    """解析 API 格式。

    优先级：自定义供应商 > 插件供应商 > 默认值 "openai"。
    """
    try:
        from query.services.api.providers import get_registry
        provider = get_registry().get_active_provider()
        if provider is not None:
            return provider.get("api_format", "openai")
    except ImportError:
        pass
    return "openai"


def _get_config_field(field: str) -> str | None:
    """从全局配置中安全读取字段。"""
    try:
        config = get_global_config()
        return getattr(config, field, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# get_default_model — 获取默认模型名
# ---------------------------------------------------------------------------


def get_default_model() -> str:
    """获取默认模型名。

    线程安全，结果缓存。
    """
    global _default_model_cache

    if _default_model_cache is not None:
        return _default_model_cache

    model = _resolve_model()
    _default_model_cache = model
    return model


# ---------------------------------------------------------------------------
# get_active_api_format - 获取当前激活供应商的 API 格式
# ---------------------------------------------------------------------------


def get_active_api_format() -> str:
    """获取当前激活供应商的 API 格式。

    返回 "openai" 或 "anthropic"，默认 "openai"。
    优先从自定义供应商取，然后是插件供应商。
    """
    # 优先从自定义供应商取
    try:
        from query.services.api.providers import get_registry
        provider = get_registry().get_active_provider()
        if provider is not None:
            return provider.get("api_format", "openai")
    except ImportError:
        pass
    return "openai"


# ---------------------------------------------------------------------------
# get_llm_client — 获取 LLM 客户端实例（单例，缓存）
# ---------------------------------------------------------------------------


def get_llm_client() -> openai.OpenAI:
    """获取 OpenAI 兼容 LLM 客户端实例。

    单例模式，线程安全。首次调用时创建实例并缓存。
    后续调用直接返回缓存实例。

    配置来源（优先级从高到低）：
      - LLMProviderRegistry 激活供应商（自定义供应商优先，然后是 llm-provider 插件）
      - 环境变量（LLM_BASE_URL / LLM_API_KEY）
      - 配置文件（~/.agent/config.json）
      - 默认值
    """
    global _client_instance

    if _client_instance is not None:
        return _client_instance

    with _client_lock:
        # 双重检查锁定
        if _client_instance is not None:
            return _client_instance

        base_url = _resolve_base_url()
        api_key = _resolve_api_key() or "sk-placeholder"

        http_client = build_http_client()

        _client_instance = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=http_client,
        )

        return _client_instance


# ---------------------------------------------------------------------------
# reset_client — 重置客户端（用于测试或配置变更后）
# ---------------------------------------------------------------------------


def reset_client() -> None:
    """重置缓存的客户端实例和模型名。

    主要用于测试场景或配置变更后需要重新创建客户端时。
    """
    global _client_instance, _default_model_cache

    with _client_lock:
        _client_instance = None
        _default_model_cache = None
