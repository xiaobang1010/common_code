"""PalaceManager - 记忆宫殿协调者。

不再直接实现 CRUD 逻辑，而是委托到四个独立模块：
  - remember/ - 存类（add_drawer, add_drawers）
  - recall/ - 查类（recall, get_drawer, list_wings, ...）
  - rethink/ - 改类（rethink）
  - forget/ - 删类（delete_drawer, delete_by_source）

同时管理底层组件的初始化：
  - ChromaStore（向量数据库）
  - JasperEmbeddingProvider（embedding 模型）
  - ClosetIndexer（搜索索引）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PalaceManager:
    """记忆宫殿协调者 - 委托到四个 CRUD 模块。

    Attributes:
        chroma_store: ChromaDB 向量存储
        embedding_provider: Jasper embedding 提供器
        closet_indexer: Closet 索引器
        remember: RememberManager
        recall: RecallManager
        rethink: RethinkManager
        forget: ForgetManager
    """

    def __init__(
        self,
        chroma_store=None,
        embedding_provider=None,
        closet_indexer=None,
    ):
        # 初始化底层组件
        if chroma_store is None:
            from memory.vector_db import ChromaStore
            chroma_store = ChromaStore()

        if embedding_provider is None:
            from memory.embedding import JasperEmbeddingProvider
            embedding_provider = JasperEmbeddingProvider()

        if closet_indexer is None:
            from memory.palace.closet import ClosetIndexer
            # ClosetIndexer 需要 storage 参数，但新架构用 ChromaStore
            # 传 None，ClosetIndexer 内部做降级处理
            try:
                closet_indexer = ClosetIndexer(chroma_store, embedding_provider)
            except Exception as e:
                logger.warning("ClosetIndexer 初始化失败: %s", e)
                closet_indexer = None

        self.chroma_store = chroma_store
        self.embedding_provider = embedding_provider
        self.closet_indexer = closet_indexer

        # 初始化四个 CRUD 管理器
        from memory.remember import RememberManager
        from memory.recall import RecallManager
        from memory.rethink import RethinkManager
        from memory.forget import ForgetManager

        self.remember = RememberManager(chroma_store, embedding_provider, closet_indexer)
        self.recall = RecallManager(chroma_store, embedding_provider, closet_indexer)
        self.rethink = RethinkManager(chroma_store, self.remember)
        self.forget = ForgetManager(chroma_store, closet_indexer)

    # --- remember 类（存）---

    def add_drawer(self, wing: str, room: str, content: str, **kwargs):
        """存记忆 - 委托到 RememberManager。"""
        return self.remember.add_drawer(wing, room, content, **kwargs)

    def add_drawers(self, drawers_data: list[dict]):
        """批量存记忆 - 委托到 RememberManager。"""
        return self.remember.add_drawers(drawers_data)

    # --- recall 类（查）---

    def recall(self, query: str, **kwargs):
        """语义搜索 - 委托到 RecallManager。"""
        return self.recall.recall(query, **kwargs)

    def get_drawer(self, drawer_id: str):
        """按 ID 查 - 委托到 RecallManager。"""
        return self.recall.get_drawer(drawer_id)

    def get_drawers_by_source(self, source_file: str):
        """按来源查 - 委托到 RecallManager。"""
        return self.recall.get_drawers_by_source(source_file)

    def list_wings(self):
        """列出 Wing - 委托到 RecallManager。"""
        return self.recall.list_wings()

    def list_rooms(self, wing: str):
        """列出 Room - 委托到 RecallManager。"""
        return self.recall.list_rooms(wing)

    def get_taxonomy(self):
        """获取分类树 - 委托到 RecallManager。"""
        return self.recall.get_taxonomy()

    def status(self):
        """获取状态 - 委托到 RecallManager。"""
        return self.recall.status()

    def list_drawers_by_importance(self, limit: int = 15, wing: str | None = None):
        """按重要性排序 - 委托到 RecallManager。"""
        return self.recall.list_drawers_by_importance(limit, wing)

    # --- rethink 类（改）---

    def rethink(self, drawer_id: str, **kwargs):
        """改记忆 - 委托到 RethinkManager。"""
        return self.rethink.rethink(drawer_id, **kwargs)

    # --- forget 类（删）---

    def delete_drawer(self, drawer_id: str):
        """删记忆 - 委托到 ForgetManager。"""
        return self.forget.delete_drawer(drawer_id)

    def delete_by_source(self, source_file: str):
        """按来源删 - 委托到 ForgetManager。"""
        return self.forget.delete_by_source(source_file)

    def delete_drawers(self, drawer_ids: list[str]):
        """批量删 - 委托到 ForgetManager。"""
        return self.forget.delete_drawers(drawer_ids)
