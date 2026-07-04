"""记忆插件协议与注册表。

MemoryProvider 是一个 Protocol（鸭子类型），插件实现以下 async 方法即可：
    - store(session_id: str, key: str, content: str) -> None
    - retrieve(session_id: str, key: str) -> str | None
    - search(query: str, limit: int = 5) -> list[dict]
    - clear(session_id: str) -> None

memory kind 的插件在目录下放 memory.py，实现 create_memory_provider(config)
工厂函数，返回 MemoryProvider 实例。
"""

from __future__ import annotations

import logging
import importlib.util
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MemoryProvider — 记忆后端协议
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryProvider(Protocol):
    """记忆后端协议（鸭子类型，不强制继承）。

    插件实现这些 async 方法即可被注册为记忆后端。
    """

    async def store(self, session_id: str, key: str, content: str) -> None:
        """存储一条记忆。

        Args:
            session_id: 会话 ID
            key: 记忆键（如 "compact_summary"）
            content: 记忆内容
        """
        ...

    async def retrieve(self, session_id: str, key: str) -> str | None:
        """检索一条记忆。

        Args:
            session_id: 会话 ID
            key: 记忆键

        Returns:
            记忆内容，不存在返回 None
        """
        ...

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索相关历史记忆。

        Args:
            query: 搜索查询
            limit: 最多返回条数

        Returns:
            [{"session_id": ..., "key": ..., "content": ..., "score": ...}, ...]
        """
        ...

    async def clear(self, session_id: str) -> None:
        """清除指定会话的所有记忆。

        Args:
            session_id: 会话 ID
        """
        ...


# ---------------------------------------------------------------------------
# MemoryRegistry — 全局注册表
# ---------------------------------------------------------------------------


class MemoryRegistry:
    """记忆后端全局注册表。

    管理已注册的记忆后端，跟踪当前激活的后端。
    同时只有一个 memory 插件激活（互斥）。
    """

    def __init__(self) -> None:
        self._providers: dict[str, MemoryProvider] = {}
        self._active: str | None = None

    def register(self, name: str, provider: MemoryProvider) -> None:
        """注册记忆后端。"""
        self._providers[name] = provider
        # 第一个注册的自动激活
        if self._active is None:
            self._active = name
        logger.info("注册记忆后端: %s", name)

    def unregister(self, name: str) -> None:
        """取消注册。"""
        self._providers.pop(name, None)
        if self._active == name:
            self._active = next(iter(self._providers), None)

    def get_active(self) -> MemoryProvider | None:
        """获取当前激活的记忆后端。None 表示无记忆插件。"""
        if self._active and self._active in self._providers:
            return self._providers[self._active]
        return None

    def get_active_name(self) -> str | None:
        """获取当前激活记忆后端名。"""
        return self._active

    def list_providers(self) -> list[str]:
        """列出所有已注册记忆后端名。"""
        return list(self._providers.keys())

    def set_active(self, name: str) -> bool:
        """切换激活记忆后端。"""
        if name not in self._providers:
            return False
        self._active = name
        return True


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_registry = MemoryRegistry()


def get_registry() -> MemoryRegistry:
    """获取全局 MemoryRegistry 单例。"""
    return _registry


def get_active_memory() -> MemoryProvider | None:
    """获取当前激活的记忆后端。None 表示无记忆插件。"""
    return _registry.get_active()


# ---------------------------------------------------------------------------
# load_memory_plugins — 加载所有 memory 插件
# ---------------------------------------------------------------------------


def load_memory_plugins() -> None:
    """加载所有启用的 memory kind 插件。

    memory 插件目录下需有 memory.py，实现 create_memory_provider(config) 工厂函数。
    """
    from startup.plugins.manager import PluginManager

    registry = get_registry()

    for plugin in PluginManager.get_enabled_by_kind("memory"):
        plugin_dir = Path(plugin.manifest.path)
        memory_file = plugin_dir / "memory.py"

        if not memory_file.is_file():
            logger.warning("memory 插件 %s 缺少 memory.py", plugin.manifest.name)
            continue

        try:
            # 动态导入 memory.py
            spec = importlib.util.spec_from_file_location(
                f"plugin_memory_{plugin.manifest.name}",
                memory_file,
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 调用工厂函数
            factory = getattr(module, "create_memory_provider", None)
            if factory is None:
                logger.warning("memory 插件 %s 缺少 create_memory_provider 函数", plugin.manifest.name)
                continue

            provider = factory({})
            registry.register(plugin.manifest.name, provider)

        except Exception as e:
            logger.exception("加载 memory 插件 %s 失败: %s", plugin.manifest.name, e)
