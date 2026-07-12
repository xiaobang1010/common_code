"""Memory Palace - 本地优先的 AI 记忆系统。

参考 mempalace 设计，实现 Palace 隐喻、四层记忆栈、混合检索引擎。
"""

from __future__ import annotations

from memory.palace.models import (
    ClosetEntry,
    Drawer,
    KGTriple,
)
from memory.palace.ids import (
    content_hash,
    generate_drawer_id,
    generate_triple_id,
)

__all__ = [
    "Drawer",
    "ClosetEntry",
    "KGTriple",
    "generate_drawer_id",
    "generate_triple_id",
    "content_hash",
]


def __getattr__(name: str):
    """懒加载 MemoryPalaceProvider，避免循环导入。"""
    if name == "MemoryPalaceProvider":
        from memory.plugin.provider import MemoryPalaceProvider
        return MemoryPalaceProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
