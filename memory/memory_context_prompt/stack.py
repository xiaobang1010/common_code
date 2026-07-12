"""MemoryContextPromptStack - 四层记忆栈统一接口。

将 L0-L3 整合为单一入口，提供分层加载策略。
"""

from __future__ import annotations

import logging
from pathlib import Path

from memory.memory_context_prompt.layer0 import Layer0
from memory.memory_context_prompt.layer1 import Layer1
from memory.memory_context_prompt.layer2 import Layer2
from memory.memory_context_prompt.layer3 import Layer3

logger = logging.getLogger(__name__)


class MemoryContextPromptStack:
    """四层记忆栈统一接口。

    将 L0-L3 整合为单一入口，提供分层加载策略。

    Args:
        chroma_store: ChromaDB 向量存储（L1/L2 使用）
        palace_manager: PalaceManager 协调者（L3 使用）
        identity_path: 身份文件路径（L0 使用）
    """

    def __init__(self, chroma_store=None, palace_manager=None,
                 identity_path: Path | None = None):
        # 如果没传 palace_manager 但有 chroma_store，用后者创建前者
        if palace_manager is None and chroma_store is None:
            from memory.vector_db import ChromaStore
            chroma_store = ChromaStore()

        if palace_manager is None:
            from memory.palace.manager import PalaceManager
            palace_manager = PalaceManager(chroma_store=chroma_store)
        elif chroma_store is None:
            # palace_manager 不为 None 时，复用其内部的 chroma_store
            chroma_store = palace_manager.chroma_store

        self.chroma_store = chroma_store
        self.palace_manager = palace_manager

        self.l0 = Layer0(identity_path)
        self.l1 = Layer1(chroma_store)
        self.l2 = Layer2(chroma_store)
        self.l3 = Layer3(palace_manager)

    def wake_up(self, wing: str | None = None) -> str:
        """唤醒 - L0 + L1，约 600-900 tokens。

        注入系统提示词，提供身份和关键故事。
        """
        parts: list[str] = []
        l0 = self.l0.render()
        if l0:
            parts.append(l0)
        l1 = self.l1.generate(wing)
        if l1:
            parts.append(l1)
        return "\n".join(parts)

    def recall(self, wing: str | None = None, room: str | None = None) -> str:
        """按需检索 - L2。"""
        return self.l2.retrieve(wing=wing, room=room)

    def search(self, query: str, wing: str | None = None,
               room: str | None = None) -> str:
        """深度搜索 - L3。"""
        return self.l3.search(query, wing=wing, room=room)

    def status(self) -> dict:
        """所有层的状态报告。

        Returns:
            {
                "l0_identity": "loaded" | "empty",
                "l1_drawers": count,
                "total_drawers": count,
                "wings": [(name, count), ...],
            }
        """
        l0_text = self.l0.render()
        total = self.chroma_store.count()
        l1_count = min(total, self.l1.MAX_DRAWERS)

        return {
            "l0_identity": "loaded" if l0_text else "empty",
            "l1_drawers": l1_count,
            "total_drawers": total,
            "wings": self.chroma_store.list_wings(),
        }
