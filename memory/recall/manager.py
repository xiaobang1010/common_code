"""recall 类 - 查记忆。

混合搜索：向量召回 + BM25 重排 + Closet boost。
以及元数据查询：get_drawer, list_wings, list_rooms, status 等。
"""

from __future__ import annotations

import logging

from memory.palace.bm25 import bm25_scores

logger = logging.getLogger(__name__)


class RecallManager:
    """查记忆管理器。

    Attributes:
        chroma_store: ChromaDB 向量存储
        embedding_provider: Jasper embedding 提供器
        closet_indexer: Closet 索引器
    """

    def __init__(self, chroma_store, embedding_provider=None, closet_indexer=None):
        self.chroma_store = chroma_store
        self.embedding_provider = embedding_provider
        self.closet_indexer = closet_indexer

    def recall(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 5,
    ) -> list[dict]:
        """混合搜索：向量召回 + BM25 重排 + Closet boost。

        流程：
        1. 构建 where 过滤条件（wing 和 room）
        2. embedding 可用时向量召回候选集（n_results * 3）
        3. embedding 不可用或召回失败时降级为纯 BM25
        4. BM25 重排并归一化到 [0, 1]
        5. Closet boost 加权
        6. 计算最终分数并排序

        Returns:
            [{"drawer_id": ..., "content": ..., "score": ...,
              "wing": ..., "room": ..., "source_file": ...}, ...]
        """
        # 构建 where 过滤条件
        where = self._build_where(wing, room)

        # 判断 embedding 是否可用
        use_vector = (
            self.embedding_provider is not None
            and self.embedding_provider.available
        )

        query_vec = None
        candidates: list[dict] = []
        # 标记是否真正使用向量分数（降级后置 False）
        use_vector_score = False

        if use_vector:
            query_vec = self.embedding_provider.embed(query)
            if query_vec is not None:
                # 向量召回候选集
                try:
                    candidates = self.chroma_store.query_drawers(
                        query_vec, n_results=n_results * 3, where=where
                    )
                    if candidates:
                        use_vector_score = True
                except Exception as e:
                    logger.warning("向量召回失败，降级到纯 BM25: %s", e)
                    candidates = []

        # 降级或向量召回失败：获取所有记录做纯 BM25
        if not candidates:
            candidates = self._get_all_drawers(wing, room)

        if not candidates:
            return []

        # BM25 重排
        documents = [c.get("content", "") for c in candidates]
        raw_bm25 = bm25_scores(query, documents)
        bm25_normalized = self._bm25_normalize(raw_bm25)

        # Closet boost 缓存：同一 source_file 只查询一次
        boost_cache: dict[str, float] = {}

        # 计算最终分数
        scored: list[dict] = []
        for i, candidate in enumerate(candidates):
            metadata = candidate.get("metadata", {})
            content = candidate.get("content", "")

            # cosine 相似度：ChromaDB cosine 距离 0=完全相同，2=完全相反
            cosine_sim = 0.0
            if use_vector_score:
                distance = candidate.get("distance", 0.0)
                cosine_sim = max(0.0, 1.0 - distance)

            bm25_score = bm25_normalized[i] if i < len(bm25_normalized) else 0.0

            # Closet boost
            closet_boost = 0.0
            if self.closet_indexer is not None:
                source_file = metadata.get("source_file", "")
                if source_file not in boost_cache:
                    boost_cache[source_file] = self.closet_indexer.get_boost_for_source(
                        source_file, query, query_vec
                    )
                closet_boost = boost_cache[source_file]

            # 最终分数
            if use_vector_score:
                score = 0.6 * cosine_sim + 0.4 * bm25_score + closet_boost
            else:
                score = 0.4 * bm25_score + closet_boost

            scored.append(
                {
                    "drawer_id": candidate.get("id", ""),
                    "content": content,
                    "score": score,
                    "wing": metadata.get("wing", ""),
                    "room": metadata.get("room", ""),
                    "source_file": metadata.get("source_file", ""),
                }
            )

        # 按分数降序排序，取 top n_results
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:n_results]

    def get_drawer(self, drawer_id: str) -> dict | None:
        """按 ID 获取单条记录。

        Returns:
            {"id": ..., "content": ..., "metadata": ...} 或 None
        """
        return self.chroma_store.get_drawer(drawer_id)

    def get_drawers_by_source(self, source_file: str) -> list[dict]:
        """按来源文件获取所有相关抽屉。"""
        return self.chroma_store.get_drawers_by_source(source_file)

    def list_wings(self) -> list[dict]:
        """列出所有 Wing。

        Returns:
            [{"name": wing_name, "drawer_count": count}, ...]
        """
        wings = self.chroma_store.list_wings()
        return [{"name": name, "drawer_count": count} for name, count in wings]

    def list_rooms(self, wing: str) -> list[dict]:
        """列出指定 Wing 下的所有 Room。

        Returns:
            [{"name": room_name, "drawer_count": count}, ...]
        """
        rooms = self.chroma_store.list_rooms(wing)
        return [{"name": name, "drawer_count": count} for name, count in rooms]

    def get_taxonomy(self) -> dict:
        """获取完整的分类树。

        Returns:
            {"wing1": {"room1": count, "room2": count}, ...}
        """
        wings = self.chroma_store.list_wings()
        taxonomy: dict[str, dict[str, int]] = {}
        for wing_name, _ in wings:
            rooms = self.chroma_store.list_rooms(wing_name)
            taxonomy[wing_name] = {room_name: count for room_name, count in rooms}
        return taxonomy

    def status(self) -> dict:
        """获取记忆宫殿整体状态。

        Returns:
            {"total_drawers": N, "total_wings": M,
             "wings": [{"name": ..., "drawer_count": ..., "rooms": [...]}, ...]}
        """
        total_drawers = self.chroma_store.count()
        wings_data = self.chroma_store.list_wings()

        wings: list[dict] = []
        for wing_name, wing_count in wings_data:
            rooms = self.chroma_store.list_rooms(wing_name)
            wings.append(
                {
                    "name": wing_name,
                    "drawer_count": wing_count,
                    "rooms": [
                        {"name": r_name, "drawer_count": r_count}
                        for r_name, r_count in rooms
                    ],
                }
            )

        return {
            "total_drawers": total_drawers,
            "total_wings": len(wings_data),
            "wings": wings,
        }

    def list_drawers_by_importance(
        self, limit: int = 15, wing: str | None = None
    ) -> list[dict]:
        """按重要性排序获取抽屉。"""
        return self.chroma_store.list_drawers_by_importance(limit, wing)

    # -----------------------------------------------------------------------
    # 内部辅助方法
    # -----------------------------------------------------------------------

    def _build_where(self, wing: str | None, room: str | None) -> dict | None:
        """构建 ChromaDB where 过滤条件。

        wing 和 room 同时指定时用 $and 组合。
        """
        conditions: list[dict] = []
        if wing is not None:
            conditions.append({"wing": wing})
        if room is not None:
            conditions.append({"room": room})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _get_all_drawers(self, wing: str | None, room: str | None) -> list[dict]:
        """获取所有抽屉（降级场景用）。

        embedding 不可用时，通过 list_drawers_by_importance 获取全部记录，
        再在内存中做 room 过滤。
        """
        # 设置大 limit 获取所有记录
        candidates = self.chroma_store.list_drawers_by_importance(
            limit=100000, wing=wing
        )
        # room 过滤（list_drawers_by_importance 不支持 room 参数）
        if room is not None:
            candidates = [
                c for c in candidates
                if c.get("metadata", {}).get("room") == room
            ]
        return candidates

    @staticmethod
    def _bm25_normalize(scores: list[float]) -> list[float]:
        """将 BM25 分数归一化到 [0, 1]。

        使用 min-max 归一化：score / max(scores) if max > 0 else 0
        """
        if not scores:
            return []
        max_score = max(scores)
        if max_score > 0:
            return [s / max_score for s in scores]
        return [0.0 for _ in scores]
