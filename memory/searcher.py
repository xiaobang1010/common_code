"""记忆宫殿混合检索引擎 - 自实现 BM25 词汇匹配 + 可选向量相似度 + Closet 加速。

混合检索流程：
  1. BM25 候选召回（FTS5 MATCH）：获取候选集（limit * 3），FTS5 分数忽略
  2. 自实现 BM25 打分：在候选集上计算 Okapi-BM25 分数（k1=1.5, b=0.75）
  3. BM25 分数归一化到 [0, 1]
  4. 向量相似度（embedding 可用时计算余弦相似度，不可用时跳过）
  5. Closet boost：对每个候选的 source_file 查询 Closet 加权分数
  6. 最终分数 = 0.6 * vector_sim + 0.4 * bm25_score + closet_boost（向量可用时）
     最终分数 = 0.4 * bm25_score + closet_boost（向量不可用时）
  7. 按最终分数降序排序，相同分数按 authored_at 降序 tie-break
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from memory.bm25 import bm25_scores, tokenize
from memory.closet import ClosetIndexer
from memory.drawer_grep import DrawerGrep
from memory.embedding import EmbeddingProvider, vector_to_bytes, bytes_to_vector
from memory.models import Drawer
from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SearchResult - 检索结果
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """混合检索结果。

    Attributes:
        drawer: 抽屉对象
        score: 最终混合分数
        bm25_score: BM25 原始分数（归一化后）
        vector_score: 向量相似度分数（0 表示未使用）
        closet_boost: Closet 加权分数
    """

    drawer: Drawer
    score: float
    bm25_score: float
    vector_score: float
    closet_boost: float


# ---------------------------------------------------------------------------
# HybridSearcher - 混合检索引擎
# ---------------------------------------------------------------------------


class HybridSearcher:
    """混合检索引擎 - 自实现 BM25 词汇匹配 + 可选向量相似度 + Closet 加速。"""

    def __init__(
        self,
        storage: PalaceStorage,
        closet_indexer: ClosetIndexer | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        drawer_grep: DrawerGrep | None = None,
    ):
        self.storage = storage
        self.closet_indexer = closet_indexer
        self.embedding_provider = embedding_provider
        self.drawer_grep = drawer_grep

    def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        source_file: str | None = None,
        limit: int = 5,
        strategy: str = "vector",
        max_distance: float | None = None,
    ) -> list[SearchResult]:
        """混合搜索。

        步骤：
        1. BM25 候选召回：调用 storage.search_fts() 获取候选集（limit * 3）
           FTS5 仅用于 MATCH 召回，分数忽略
        2. 按 (source_file, chunk_index) 去重候选集
        3. union 策略时注入 BM25-only 候选（可选 max_distance 过滤）
        4. 自实现 BM25 打分：在候选集上计算 Okapi-BM25 分数
        5. BM25 分数归一化到 [0, 1]
        6. 可选向量相似度（embedding 可用时计算余弦相似度，不可用时跳过，vector_score = 0）
        7. Closet boost：对每个候选的 source_file 查询 Closet boost
        8. 最终分数 = 0.4 * bm25_score + closet_boost（向量不可用时）
           最终分数 = 0.6 * vector_score + 0.4 * bm25_score + closet_boost（向量可用时）
        9. 按最终分数降序排序，相同分数按 authored_at 降序 tie-break

        Args:
            query: 搜索查询文本
            wing: 顶层命名空间过滤，None 不过滤
            room: 子分类过滤，None 不过滤
            source_file: 来源文件过滤，None 不过滤
            limit: 返回上限
            strategy: 候选策略，"vector"（默认）或 "union"（BM25-only 候选注入）
            max_distance: 最大距离阈值，设定时跳过无向量嵌入的 BM25-only 候选
        """
        # Step 1: FTS5 MATCH 候选召回（分数忽略）
        candidates = self.storage.search_fts(
            query, wing=wing, room=room, source_file=source_file,
            limit=limit * 3,
        )

        if not candidates:
            return []

        # 按 (source_file, chunk_index) 去重，避免不同目录同名文件被误合并
        seen_keys: set[tuple[str, int]] = set()
        deduped_candidates: list[tuple[Drawer, float]] = []
        for drawer, score in candidates:
            key = (drawer.source_file, drawer.chunk_index)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_candidates.append((drawer, score))
        candidates = deduped_candidates

        # union 策略：注入 BM25-only 候选
        if strategy == "union":
            extra = self._inject_bm25_candidates(
                query, wing=wing, room=room, source_file=source_file,
                seen_keys=seen_keys,
            )
            # max_distance 保护：设定距离阈值时跳过无向量嵌入的 BM25-only 候选
            if max_distance is not None:
                extra = [
                    (d, s) for d, s in extra
                    if self.storage.get_embedding(d.id) is not None
                ]
            candidates.extend(extra)

        # 提取候选抽屉（FTS5 分数忽略）
        drawers = [d for d, _ in candidates]

        # Step 4: 自实现 BM25 打分
        documents = [d.content for d in drawers]
        raw_bm25 = bm25_scores(query, documents, k1=1.5, b=0.75)

        # Step 5: BM25 分数归一化到 [0, 1]
        normalized = self._bm25_normalize(raw_bm25)

        # Step 6-8: 计算最终分数
        # Closet boost 缓存：同一 source_file 只查询一次
        boost_cache: dict[str, float] = {}
        results: list[SearchResult] = []

        # Step 6: 向量相似度 - 嵌入查询向量
        query_vec = None
        use_vector = (
            self.embedding_provider is not None
            and self.embedding_provider.available
        )
        if use_vector:
            query_vec = self.embedding_provider.embed(query)

        for drawer, bm25_score in zip(drawers, normalized):
            # Step 6: 向量相似度
            vector_score = 0.0
            if use_vector and query_vec:
                drawer_vec_bytes = self.storage.get_embedding(drawer.id)
                if drawer_vec_bytes:
                    drawer_vec = bytes_to_vector(drawer_vec_bytes)
                    vector_score = self._cosine_similarity(query_vec, drawer_vec)

            # Step 7: Closet boost
            closet_boost = 0.0
            if self.closet_indexer is not None:
                sf = drawer.source_file
                if sf not in boost_cache:
                    boost_cache[sf] = self.closet_indexer.get_boost_for_source(
                        sf, query, query_vec
                    )
                closet_boost = boost_cache[sf]

            # DrawerGrep 富化：Closet 命中且来源文件有多个 chunk 时扩展上下文
            if closet_boost > 0 and self.drawer_grep:
                drawers_for_source = self.storage.list_drawers(
                    source_file=drawer.source_file, limit=1000
                )
                if len(drawers_for_source) > 1:
                    enriched = self.drawer_grep.enrich(
                        drawer.source_file, tokenize(query)
                    )
                    if enriched:
                        drawer = Drawer(
                            **{**drawer.__dict__, "content": enriched}
                        )

            # Step 8: 最终分数
            if use_vector:
                final_score = 0.6 * vector_score + 0.4 * bm25_score + closet_boost
            else:
                final_score = 0.4 * bm25_score + closet_boost

            results.append(
                SearchResult(
                    drawer=drawer,
                    score=final_score,
                    bm25_score=bm25_score,
                    vector_score=vector_score,
                    closet_boost=closet_boost,
                )
            )

        # Step 9: 按最终分数降序排序，相同分数按 authored_at 降序 tie-break
        results.sort(
            key=lambda r: (r.score, r.drawer.authored_at), reverse=True
        )
        return results[:limit]

    def search_raw(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        source_file: str | None = None,
        limit: int = 5,
        strategy: str = "vector",
        max_distance: float | None = None,
    ) -> list[dict]:
        """返回结构化 dict 列表而非 SearchResult 对象。"""
        results = self.search(
            query, wing=wing, room=room, source_file=source_file,
            limit=limit, strategy=strategy, max_distance=max_distance,
        )
        return [
            {
                "drawer": r.drawer,
                "score": r.score,
                "bm25_score": r.bm25_score,
                "vector_score": r.vector_score,
                "closet_boost": r.closet_boost,
            }
            for r in results
        ]

    def _inject_bm25_candidates(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        source_file: str | None = None,
        seen_keys: set[tuple[str, int]] | None = None,
    ) -> list[tuple[Drawer, float]]:
        """注入 BM25-only 候选（union 策略）。

        扫描所有匹配过滤条件的抽屉，找出内容包含查询词但未被 FTS5 召回的抽屉，
        返回 (drawer, 0.0) 元组列表。按 (source_file, chunk_index) 去重。

        Args:
            query: 搜索查询文本
            wing: 顶层命名空间过滤
            room: 子分类过滤
            source_file: 来源文件过滤
            seen_keys: 已有候选的去重 key 集合

        Returns:
            BM25-only 候选列表 [(drawer, 0.0), ...]
        """
        query_terms = set(tokenize(query))
        if not query_terms:
            return []

        if seen_keys is None:
            seen_keys = set()

        # 扫描所有匹配过滤条件的抽屉
        all_drawers = self.storage.list_drawers(
            wing=wing, room=room, source_file=source_file, limit=100000
        )

        extra: list[tuple[Drawer, float]] = []
        for drawer in all_drawers:
            key = (drawer.source_file, drawer.chunk_index)
            if key in seen_keys:
                continue

            # 检查内容是否包含任何查询词
            doc_terms = set(tokenize(drawer.content))
            if doc_terms & query_terms:
                extra.append((drawer, 0.0))
                seen_keys.add(key)

        return extra

    @staticmethod
    def _bm25_normalize(scores: list[float]) -> list[float]:
        """将 BM25 分数归一化到 [0, 1]。

        使用 min-max 归一化：score / max(scores) if max > 0 else 0

        处理边界情况：
        - 空列表：返回空列表
        - 单元素列表：max > 0 时归一化为 1.0，max == 0 时返回 0.0
        """
        if not scores:
            return []

        max_score = max(scores)
        if max_score > 0:
            return [s / max_score for s in scores]
        return [0.0 for _ in scores]

    @staticmethod
    def _distance_to_similarity(distance: float, metric: str = "cosine") -> float:
        """将距离转换为 [0, 1] 相似度。"""
        import math
        if metric == "l2":
            return 1.0 / (1.0 + max(0.0, distance))
        if metric == "ip":
            return 1.0 / (1.0 + math.exp(min(60.0, distance)))
        # cosine (default)
        return max(0.0, 1.0 - distance)

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """计算两个向量的余弦相似度。

        Args:
            vec_a: 向量 A
            vec_b: 向量 B

        Returns:
            余弦相似度 [0, 1]，维度不匹配或零向量返回 0.0
        """
        if len(vec_a) != len(vec_b) or not vec_a:
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return max(0.0, dot / (norm_a * norm_b))
