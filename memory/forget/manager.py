"""forget 类 - 删记忆。

删除抽屉，支持单块和分块批量删除。
删除父 ID 时自动收集并删除所有分块。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ForgetManager:
    """删记忆管理器。

    负责从 ChromaDB 中删除抽屉记录，支持：
    - 单个抽屉删除（含分块自动收集）
    - 按来源文件批量删除
    - 多抽屉批量删除（含各自的分块）

    Attributes:
        chroma_store: ChromaDB 向量存储
        closet_indexer: Closet 索引器（可选，用于清理 Closet 条目）
    """

    def __init__(self, chroma_store, closet_indexer=None):
        self.chroma_store = chroma_store
        self.closet_indexer = closet_indexer

    def delete_drawer(self, drawer_id: str) -> int:
        """删除单个抽屉，返回删除的物理记录数。

        如果 drawer_id 是父 ID，会自动收集并删除所有分块。

        Args:
            drawer_id: 抽屉 ID

        Returns:
            删除的记录数（0 表示无记录可删）
        """
        # 检查 drawer_id 是否直接存在
        existing = self.chroma_store.get_drawer(drawer_id)

        # 检查是否有分块（drawer_id 作为父 ID）
        chunks = self.chroma_store.get_chunks(drawer_id)

        # 收集所有要删除的 ID，用 set 去重防御性处理
        all_ids: set[str] = set()
        source_file = None

        if existing is not None:
            all_ids.add(drawer_id)
            source_file = existing.get("metadata", {}).get("source_file")

        for chunk in chunks:
            chunk_id = chunk.get("id")
            if chunk_id:
                all_ids.add(chunk_id)

        # 列表为空则无需删除
        if not all_ids:
            return 0

        # 批量删除
        self.chroma_store.delete_drawers(list(all_ids))

        # 清理 Closet 条目（如果有 closet_indexer 且有 source_file）
        if self.closet_indexer is not None and source_file:
            self.closet_indexer.remove_by_source(source_file)

        logger.debug("delete_drawer %s -> %d 条记录已删除", drawer_id, len(all_ids))

        return len(all_ids)

    def delete_by_source(self, source_file: str) -> int:
        """按来源文件删除所有相关记录，返回删除的记录数。

        Args:
            source_file: 来源文件路径

        Returns:
            删除的记录数
        """
        # 先清理 Closet 条目
        if self.closet_indexer is not None:
            self.closet_indexer.remove_by_source(source_file)

        # 从 ChromaDB 删除
        count = self.chroma_store.delete_by_source(source_file)

        logger.debug("delete_by_source %s -> %d 条记录已删除", source_file, count)

        return count

    def delete_drawers(self, drawer_ids: list[str]) -> int:
        """批量删除多个抽屉（含各自的分块）。

        对每个 drawer_id 收集直接记录和分块记录，
        合并去重后批量删除。

        Args:
            drawer_ids: 抽屉 ID 列表

        Returns:
            删除的记录总数
        """
        all_ids: set[str] = set()

        for drawer_id in drawer_ids:
            # 收集直接记录
            existing = self.chroma_store.get_drawer(drawer_id)
            if existing is not None:
                all_ids.add(drawer_id)

            # 收集分块记录
            chunks = self.chroma_store.get_chunks(drawer_id)
            for chunk in chunks:
                chunk_id = chunk.get("id")
                if chunk_id:
                    all_ids.add(chunk_id)

        if not all_ids:
            return 0

        # 批量删除
        self.chroma_store.delete_drawers(list(all_ids))

        logger.debug(
            "delete_drawers %d 个 ID -> %d 条记录已删除",
            len(drawer_ids),
            len(all_ids),
        )

        return len(all_ids)
