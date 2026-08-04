"""LLM 供应商注册表 - 管理多个 LLM 供应商配置，支持运行时切换。

llm-provider kind 的插件在 manifest 中声明 llm_provider 字段，
加载后注册到这里。用户可通过 API 切换当前激活的供应商。

除了插件供应商外，还支持自定义供应商（来自配置文件的 llm_providers 列表）。
自定义供应商优先级高于插件供应商。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from startup.plugins.manifest import LLMProviderConfig
from startup.config import CustomLLMProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLMProviderRegistry - 全局注册表
# ---------------------------------------------------------------------------


class LLMProviderRegistry:
    """LLM 供应商全局注册表。

    管理两类供应商：
      1. 自定义供应商（来自配置文件 llm_providers，优先级更高）
      2. 插件供应商（通过 register() 注册，来自 llm-provider 插件）

    切换激活供应商时调用 reset_client() 清除客户端缓存。

    Attributes:
        _providers: name -> LLMProviderConfig 映射（插件供应商）
        _custom_providers: id -> CustomLLMProvider 映射（自定义供应商）
        _active: 当前激活的供应商标识（自定义供应商的 id 或插件供应商的 name）
        _active_model: 当前激活的模型 ID（仅对自定义供应商有意义）
        _lock: 线程锁
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProviderConfig] = {}
        self._custom_providers: dict[str, CustomLLMProvider] = {}
        self._active: str | None = None
        self._active_model: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 注册管理 - 插件供应商
    # ------------------------------------------------------------------

    def register(self, config: LLMProviderConfig) -> None:
        """注册一个插件 LLM 供应商。"""
        with self._lock:
            self._providers[config.name] = config
            # 第一个注册的自动设为激活（仅当没有自定义供应商时）
            if self._active is None and not self._custom_providers:
                self._active = config.name
            logger.info("注册插件 LLM 供应商: %s (base_url=%s)", config.name, config.base_url)

    def unregister(self, name: str) -> None:
        """取消注册。"""
        with self._lock:
            self._providers.pop(name, None)
            self._custom_providers.pop(name, None)
            if self._active == name:
                # 优先切换到自定义供应商
                self._active = next(iter(self._custom_providers), None) or next(
                    iter(self._providers), None
                )

    # ------------------------------------------------------------------
    # 注册管理 - 自定义供应商
    # ------------------------------------------------------------------

    def register_custom(self, config: CustomLLMProvider) -> None:
        """注册一个自定义 LLM 供应商。

        自定义供应商优先级高于插件供应商，注册后自动设为激活（如果是第一个）。
        """
        with self._lock:
            self._custom_providers[config.id] = config
            # 第一个注册的自动设为激活
            if self._active is None:
                self._active = config.id
            logger.info(
                "注册自定义 LLM 供应商: %s (base_url=%s)", config.name, config.base_url
            )

    def load_custom_providers(self, providers_data: list[dict]) -> None:
        """从配置数据批量加载自定义供应商。

        Args:
            providers_data: 配置文件中的 llm_providers 列表，每项是一个供应商的 dict
        """
        for data in providers_data:
            try:
                provider = CustomLLMProvider.from_dict(data)
                self.register_custom(provider)
            except Exception as e:
                logger.warning("解析自定义 LLM 供应商配置失败: %s, 错误: %s", data, e)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_provider(self, name: str) -> LLMProviderConfig | None:
        """获取指定插件供应商配置。"""
        with self._lock:
            return self._providers.get(name)

    def get_active_provider(self) -> dict[str, Any] | None:
        """获取当前激活的供应商配置，返回统一格式的 dict。

        优先返回自定义供应商，然后才是插件供应商。
        若无注册供应商，返回 None（由调用方降级到环境变量/默认值）。

        返回格式：
            {
                "id": "...",
                "name": "...",
                "base_url": "...",
                "api_key": "...",
                "model": "...",       # 当前模型
                "models": [...],      # 模型列表
                "api_format": "openai" | "anthropic",
                "source": "custom" | "plugin",
            }
        """
        with self._lock:
            # 优先查找自定义供应商
            if self._active and self._active in self._custom_providers:
                p = self._custom_providers[self._active]
                model_ids = [m.model_id for m in p.models]
                # 确定当前模型：优先用 active_model，其次取第一个模型
                model = ""
                if self._active_model and self._active_model in model_ids:
                    model = self._active_model
                elif model_ids:
                    model = model_ids[0]
                return {
                    "id": p.id,
                    "name": p.name,
                    "base_url": p.base_url,
                    "api_key": p.api_key,
                    "model": model,
                    "models": model_ids,
                    "api_format": p.api_format,
                    "source": "custom",
                }
            # 然后查找插件供应商
            if self._active and self._active in self._providers:
                p = self._providers[self._active]
                return {
                    "id": p.name,
                    "name": p.name,
                    "base_url": p.base_url,
                    "api_key": p.api_key,
                    "model": p.model,
                    "models": p.models,
                    "api_format": "openai",
                    "source": "plugin",
                }
            return None

    def get_active_name(self) -> str | None:
        """获取当前激活供应商标识。

        优先返回自定义供应商的 ID，然后才是插件供应商的 name。
        """
        with self._lock:
            if self._active is None:
                return None
            # 优先检查自定义供应商
            if self._active in self._custom_providers:
                return self._active
            # 然后检查插件供应商
            if self._active in self._providers:
                return self._active
            return None

    def list_providers(self) -> list[dict[str, Any]]:
        """列出所有已注册供应商（自定义 + 插件）。

        自定义供应商在列表中标记 source="custom"，
        插件供应商标记 source="plugin"。
        自定义供应商排在前面。
        """
        with self._lock:
            result: list[dict[str, Any]] = []
            # 自定义供应商
            for p in self._custom_providers.values():
                model_ids = [m.model_id for m in p.models]
                # 当前激活的自定义供应商使用 active_model，否则取第一个
                model = ""
                if self._active == p.id:
                    if self._active_model and self._active_model in model_ids:
                        model = self._active_model
                    elif model_ids:
                        model = model_ids[0]
                elif model_ids:
                    model = model_ids[0]
                result.append(
                    {
                        "id": p.id,
                        "name": p.name,
                        "base_url": p.base_url,
                        "api_key": p.api_key,
                        "model": model,
                        "models": model_ids,
                        "api_format": p.api_format,
                        "source": "custom",
                    }
                )
            # 插件供应商
            for p in self._providers.values():
                result.append(
                    {
                        "id": p.name,
                        "name": p.name,
                        "base_url": p.base_url,
                        "api_key": p.api_key,
                        "model": p.model,
                        "models": p.models,
                        "api_format": "openai",
                        "source": "plugin",
                    }
                )
            return result

    # ------------------------------------------------------------------
    # 切换
    # ------------------------------------------------------------------

    def set_active(self, name: str) -> bool:
        """切换当前激活供应商。

        支持自定义供应商 ID 和插件供应商 name。
        切换后调用 reset_client() 清除客户端缓存。

        Returns:
            True 成功，False 供应商不存在
        """
        with self._lock:
            # 先检查自定义供应商，再检查插件供应商
            if name not in self._custom_providers and name not in self._providers:
                return False
            self._active = name

        # 清除 LLM 客户端缓存
        try:
            from query.services.api.client import reset_client
            reset_client()
        except ImportError:
            pass

        logger.info("切换 LLM 供应商: %s", name)
        return True

    def set_active_from_config(self, name: str) -> None:
        """从配置文件恢复激活供应商（启动时调用）。

        不触发 reset_client（因为客户端还没创建）。
        """
        with self._lock:
            if name in self._custom_providers or name in self._providers:
                self._active = name
            elif self._custom_providers:
                # 配置的供应商不存在，优先用第一个自定义供应商
                self._active = next(iter(self._custom_providers))
            elif self._providers:
                # 其次用第一个插件供应商
                self._active = next(iter(self._providers))
            logger.debug("从配置恢复 LLM 供应商: %s", self._active)

    def set_active_model(self, model_id: str) -> None:
        """设置当前激活的模型 ID（仅对自定义供应商有意义）。

        设置后清除客户端缓存，让下次请求使用新模型。
        """
        with self._lock:
            self._active_model = model_id

        # 清除 LLM 客户端缓存
        try:
            from query.services.api.client import reset_client
            reset_client()
        except ImportError:
            pass

        logger.info("切换 LLM 模型: %s", model_id)


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_registry = LLMProviderRegistry()


def get_registry() -> LLMProviderRegistry:
    """获取全局 LLMProviderRegistry 单例。"""
    return _registry


# ---------------------------------------------------------------------------
# load_llm_provider_plugins - 加载所有 llm-provider 插件和自定义供应商
# ---------------------------------------------------------------------------


def load_llm_provider_plugins() -> None:
    """加载所有启用的 llm-provider kind 插件，注册到 registry。

    同时从全局配置加载自定义供应商（llm_providers 列表），
    并恢复配置中记录的激活供应商和模型。
    """
    from startup.plugins.manager import PluginManager

    registry = get_registry()

    # 1. 加载插件供应商
    for plugin in PluginManager.get_enabled_by_kind("llm-provider"):
        if plugin.manifest.llm_provider is None:
            continue
        registry.register(plugin.manifest.llm_provider)

    # 2. 加载自定义供应商（来自配置文件）
    try:
        from startup.config import get_global_config

        config = get_global_config()
        if config.llm_providers:
            registry.load_custom_providers(config.llm_providers)

        # 3. 从配置恢复激活的供应商和模型
        if config.active_provider:
            registry.set_active_from_config(config.active_provider)
        if config.active_model:
            registry.set_active_model(config.active_model)
    except Exception as e:
        logger.warning("加载自定义 LLM 供应商失败: %s", e)
