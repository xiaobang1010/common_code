"""LLM 供应商注册表 — 管理多个 LLM 供应商配置，支持运行时切换。

llm-provider kind 的插件在 manifest 中声明 llm_provider 字段，
加载后注册到这里。用户可通过 API 切换当前激活的供应商。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from startup.plugins.manifest import LLMProviderConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLMProviderRegistry — 全局注册表
# ---------------------------------------------------------------------------


class LLMProviderRegistry:
    """LLM 供应商全局注册表。

    管理所有已注册的供应商配置，跟踪当前激活的供应商。
    切换激活供应商时调用 reset_client() 清除客户端缓存。

    Attributes:
        _providers: name → LLMProviderConfig 映射
        _active: 当前激活的供应商名
        _lock: 线程锁
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProviderConfig] = {}
        self._active: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 注册管理
    # ------------------------------------------------------------------

    def register(self, config: LLMProviderConfig) -> None:
        """注册一个 LLM 供应商。"""
        with self._lock:
            self._providers[config.name] = config
            # 第一个注册的自动设为激活
            if self._active is None:
                self._active = config.name
            logger.info("注册 LLM 供应商: %s (base_url=%s)", config.name, config.base_url)

    def unregister(self, name: str) -> None:
        """取消注册。"""
        with self._lock:
            self._providers.pop(name, None)
            if self._active == name:
                self._active = next(iter(self._providers), None)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_provider(self, name: str) -> LLMProviderConfig | None:
        """获取指定供应商配置。"""
        with self._lock:
            return self._providers.get(name)

    def get_active_provider(self) -> LLMProviderConfig | None:
        """获取当前激活的供应商配置。

        若无注册供应商，返回 None（由调用方降级到环境变量/默认值）。
        """
        with self._lock:
            if self._active and self._active in self._providers:
                return self._providers[self._active]
            return None

    def get_active_name(self) -> str | None:
        """获取当前激活供应商名。"""
        with self._lock:
            return self._active

    def list_providers(self) -> list[dict[str, Any]]:
        """列出所有已注册供应商。"""
        with self._lock:
            return [
                {
                    "name": p.name,
                    "base_url": p.base_url,
                    "model": p.model,
                    "models": p.models,
                }
                for p in self._providers.values()
            ]

    # ------------------------------------------------------------------
    # 切换
    # ------------------------------------------------------------------

    def set_active(self, name: str) -> bool:
        """切换当前激活供应商。

        切换后调用 reset_client() 清除客户端缓存。

        Returns:
            True 成功，False 供应商不存在
        """
        with self._lock:
            if name not in self._providers:
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
            if name in self._providers:
                self._active = name
            elif self._providers:
                # 配置的供应商不存在，用第一个注册的
                self._active = next(iter(self._providers))
            logger.debug("从配置恢复 LLM 供应商: %s", self._active)


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_registry = LLMProviderRegistry()


def get_registry() -> LLMProviderRegistry:
    """获取全局 LLMProviderRegistry 单例。"""
    return _registry


# ---------------------------------------------------------------------------
# load_llm_provider_plugins — 加载所有 llm-provider 插件
# ---------------------------------------------------------------------------


def load_llm_provider_plugins() -> None:
    """加载所有启用的 llm-provider kind 插件，注册到 registry。"""
    from startup.plugins.manager import PluginManager

    registry = get_registry()

    for plugin in PluginManager.get_enabled_by_kind("llm-provider"):
        if plugin.manifest.llm_provider is None:
            continue
        registry.register(plugin.manifest.llm_provider)
