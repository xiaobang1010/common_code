"""memory-palace 插件 - Memory Palace 记忆后端工厂。

实现 create_memory_provider(config) 工厂函数，
返回 MemoryPalaceProvider 实例，注册到 MemoryRegistry。
"""

from __future__ import annotations

from typing import Any


def create_memory_provider(config: dict[str, Any]):
    """创建 MemoryPalaceProvider 实例。

    Args:
        config: 配置字典（当前未使用，预留扩展）

    Returns:
        MemoryPalaceProvider 实例
    """
    from memory.plugin.provider import MemoryPalaceProvider

    return MemoryPalaceProvider()
