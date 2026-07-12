"""Memory Palace - 本地优先的 AI 记忆系统。

参考 mempalace 设计，实现 Palace 隐喻、四层记忆栈、混合检索引擎。
"""

from __future__ import annotations

from memory.models import (
    ClosetEntry,
    Drawer,
    KGTriple,
    content_hash,
    generate_drawer_id,
    generate_triple_id,
)
from memory.storage import PalaceStorage

__all__ = [
    "Drawer",
    "ClosetEntry",
    "KGTriple",
    "PalaceStorage",
    "MemoryPalaceProvider",
    "generate_drawer_id",
    "generate_triple_id",
    "content_hash",
]


def __getattr__(name: str):
    """懒加载 MemoryPalaceProvider，避免循环导入。

    MemoryPalaceProvider 依赖 storage 和 models，而 storage 不依赖 provider，
    通过 __getattr__ 延迟导入，仅在首次访问时加载。
    """
    if name == "MemoryPalaceProvider":
        from memory.provider import MemoryPalaceProvider

        return MemoryPalaceProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
