"""向量数据库存储层 - ChromaDB PersistentClient。

管理抽屉的文档、向量、元数据存储，提供 HNSW 向量搜索。
数据目录：~/.agent/memory/chroma/
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ChromaDB 数据目录
CHROMA_DIR = Path.home() / ".agent" / "memory" / "chroma"
# Collection 名称
COLLECTION_NAME = "drawers"

# 无向量记录标记：chromadb 1.5.9 实测 `embeddings=[None]` 会抛 ValueError，
# 省略 embeddings 又会被默认 embedding function 自动生成向量，无法表达"无向量"。
# 因此无向量记录用零向量占位 + metadata 标记，query 时按标记排除。
_NO_EMBEDDING_KEY = "_no_embedding"
# 占位向量维度：与 Jasper 截断后输出维度一致（384），保证与 collection 维度匹配
_PLACEHOLDER_DIM = 384


def _mark_if_missing(embedding, metadata: dict) -> tuple[list[float], dict]:
    """embedding 为 None 时换成零向量占位并附加标记，否则标记为有向量。"""
    if embedding is None:
        return [0.0] * _PLACEHOLDER_DIM, {**metadata, _NO_EMBEDDING_KEY: True}
    return embedding, {**metadata, _NO_EMBEDDING_KEY: False}


def _merge_where(base: dict | None, extra: dict) -> dict:
    """合并 where 条件：base 为空时直接用 extra，否则用 $and 组合。"""
    if not base:
        return extra
    return {"$and": [base, extra]}


def _sanitize_metadata(metadata: dict) -> dict:
    """清理元数据，确保所有值都是 ChromaDB 支持的类型。

    ChromaDB 的元数据值只能是 str、int、float、bool。
    None 值转为空字符串 ""。
    """
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            result[key] = ""
        else:
            result[key] = value
    return result


class ChromaStore:
    """ChromaDB 向量存储 - 管理抽屉的文档、向量、元数据。

    使用 PersistentClient 持久化存储，
    HNSW 索引 + cosine 距离。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._available = False
        self._client = None
        self._collection = None

        try:
            import chromadb
        except ImportError:
            logger.info("chromadb 未安装，向量存储不可用")
            return

        # 确定数据目录
        path = db_path if db_path is not None else CHROMA_DIR
        try:
            path.mkdir(parents=True, exist_ok=True)
            # 创建 PersistentClient
            self._client = chromadb.PersistentClient(path=str(path))
            # 获取或创建 collection，使用 cosine 距离
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info(
                "ChromaDB 初始化成功: path=%s, collection=%s",
                path,
                COLLECTION_NAME,
            )
        except Exception as e:
            logger.warning("ChromaDB 初始化失败: %s", e)
            self._available = False
            self._client = None
            self._collection = None

    @property
    def available(self) -> bool:
        """ChromaDB 是否可用。"""
        return self._available

    def upsert_drawer(
        self,
        drawer_id: str,
        content: str,
        embedding: list[float] | None,
        metadata: dict,
    ) -> bool:
        """将单条文档、向量、元数据原子写入 ChromaDB。

        embedding 为 None 时（模型不可用）用零向量占位并附加
        _no_embedding 标记，query 时被排除，待模型就绪后补嵌。
        """
        if not self._available:
            return False
        try:
            embedding, metadata = _mark_if_missing(embedding, metadata)
            self._collection.upsert(
                ids=[drawer_id],
                documents=[content],
                embeddings=[embedding],
                metadatas=[_sanitize_metadata(metadata)],
            )
            return True
        except Exception as e:
            logger.warning("upsert_drawer 失败: %s", e)
            return False

    def upsert_drawers(
        self,
        ids: list[str],
        contents: list[str],
        embeddings: list[list[float] | None],
        metadatas: list[dict],
    ) -> bool:
        """批量 upsert（原子操作）。None 向量处理同 upsert_drawer。"""
        if not self._available:
            return False
        try:
            filled_embeddings: list[list[float]] = []
            filled_metadatas: list[dict] = []
            for embedding, metadata in zip(embeddings, metadatas):
                emb, meta = _mark_if_missing(embedding, metadata)
                filled_embeddings.append(emb)
                filled_metadatas.append(meta)
            self._collection.upsert(
                ids=ids,
                documents=contents,
                embeddings=filled_embeddings,
                metadatas=[_sanitize_metadata(m) for m in filled_metadatas],
            )
            return True
        except Exception as e:
            logger.warning("upsert_drawers 失败: %s", e)
            return False

    def query_drawers(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """向量搜索 + 元数据过滤。

        Returns:
            [{"id": ..., "content": ..., "metadata": ..., "distance": ...}, ...]
        """
        if not self._available:
            return []
        try:
            # 排除无向量占位记录，避免零向量污染检索结果。
            # 注意用 $ne: True 而不是 False：改造前写入的存量记录没有
            # _no_embedding 键，where {键: False} 不会命中缺键记录，
            # 会误伤全部历史记忆；$ne 写法能同时命中缺键旧记录与有向量新记录。
            full_where = _merge_where(where, {_NO_EMBEDDING_KEY: {"$ne": True}})
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=full_where,
                include=["documents", "metadatas", "distances"],
            )
            # ChromaDB query 返回嵌套 list（支持多查询），取第一个查询结果
            ids_list = result.get("ids", [[]])[0]
            documents_list = result.get("documents", [[]])[0]
            metadatas_list = result.get("metadatas", [[]])[0]
            distances_list = result.get("distances", [[]])[0]

            results: list[dict] = []
            for i, drawer_id in enumerate(ids_list):
                results.append(
                    {
                        "id": drawer_id,
                        "content": documents_list[i] if i < len(documents_list) else "",
                        "metadata": metadatas_list[i] if i < len(metadatas_list) else {},
                        "distance": distances_list[i] if i < len(distances_list) else 0.0,
                    }
                )
            return results
        except Exception as e:
            logger.warning("query_drawers 失败: %s", e)
            return []

    def get_drawer(self, drawer_id: str) -> dict | None:
        """按 ID 获取单条记录。

        Returns:
            {"id": ..., "content": ..., "metadata": ..., "embedding": ...} 或 None
        """
        if not self._available:
            return None
        try:
            result = self._collection.get(
                ids=[drawer_id],
                include=["documents", "metadatas", "embeddings"],
            )
            ids_list = result.get("ids", [])
            if not ids_list:
                return None
            documents_list = result.get("documents", [])
            metadatas_list = result.get("metadatas", [])
            embeddings_list = result.get("embeddings", [])
            return {
                "id": ids_list[0],
                "content": documents_list[0] if documents_list else "",
                "metadata": metadatas_list[0] if metadatas_list else {},
                "embedding": embeddings_list[0] if embeddings_list else [],
            }
        except Exception as e:
            logger.warning("get_drawer 失败: %s", e)
            return None

    def get_missing_embeddings(self, limit: int = 1000) -> list[dict]:
        """获取无向量记录（_no_embedding 标记为 True），用于加载完成后补嵌。

        Returns:
            [{"id": ..., "content": ..., "metadata": ...}, ...]
        """
        if not self._available:
            return []
        try:
            result = self._collection.get(
                where={_NO_EMBEDDING_KEY: True},
                limit=limit,
                include=["documents", "metadatas"],
            )
            ids_list = result.get("ids", [])
            documents_list = result.get("documents", [])
            metadatas_list = result.get("metadatas", [])

            results: list[dict] = []
            for i, drawer_id in enumerate(ids_list):
                results.append(
                    {
                        "id": drawer_id,
                        "content": documents_list[i] if i < len(documents_list) else "",
                        "metadata": metadatas_list[i] if i < len(metadatas_list) else {},
                    }
                )
            return results
        except Exception as e:
            logger.warning("get_missing_embeddings 失败: %s", e)
            return []

    def update_embeddings(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> bool:
        """给已有记录补挂向量并更新元数据（清除 _no_embedding 标记）。

        Args:
            ids: 记录 id 列表
            embeddings: 与 ids 等长的向量列表（不含 None，调用方已过滤）
            metadatas: 与 ids 等长的元数据列表（调用方需清除 _no_embedding 标记）

        Returns:
            是否全部成功
        """
        if not self._available or not ids:
            return False
        try:
            self._collection.update(
                ids=ids,
                embeddings=embeddings,
                metadatas=[_sanitize_metadata(m) for m in metadatas],
            )
            return True
        except Exception as e:
            logger.warning("update_embeddings 失败: %s", e)
            return False

    def get_drawers_by_ids(self, drawer_ids: list[str]) -> list[dict]:
        """批量按 ID 获取。"""
        if not self._available or not drawer_ids:
            return []
        try:
            result = self._collection.get(
                ids=drawer_ids,
                include=["documents", "metadatas"],
            )
            ids_list = result.get("ids", [])
            documents_list = result.get("documents", [])
            metadatas_list = result.get("metadatas", [])

            results: list[dict] = []
            for i, drawer_id in enumerate(ids_list):
                results.append(
                    {
                        "id": drawer_id,
                        "content": documents_list[i] if i < len(documents_list) else "",
                        "metadata": metadatas_list[i] if i < len(metadatas_list) else {},
                    }
                )
            return results
        except Exception as e:
            logger.warning("get_drawers_by_ids 失败: %s", e)
            return []

    def get_drawers_by_source(self, source_file: str, limit: int = 1000) -> list[dict]:
        """按 source_file 元数据过滤获取。"""
        if not self._available:
            return []
        try:
            result = self._collection.get(
                where={"source_file": source_file},
                limit=limit,
                include=["documents", "metadatas"],
            )
            ids_list = result.get("ids", [])
            documents_list = result.get("documents", [])
            metadatas_list = result.get("metadatas", [])

            results: list[dict] = []
            for i, drawer_id in enumerate(ids_list):
                results.append(
                    {
                        "id": drawer_id,
                        "content": documents_list[i] if i < len(documents_list) else "",
                        "metadata": metadatas_list[i] if i < len(metadatas_list) else {},
                    }
                )
            return results
        except Exception as e:
            logger.warning("get_drawers_by_source 失败: %s", e)
            return []

    def get_chunks(self, parent_drawer_id: str) -> list[dict]:
        """获取父抽屉的所有分块。"""
        if not self._available:
            return []
        try:
            result = self._collection.get(
                where={"parent_drawer_id": parent_drawer_id},
                limit=10000,
                include=["documents", "metadatas", "embeddings"],
            )
            ids_list = result.get("ids", [])
            documents_list = result.get("documents", [])
            metadatas_list = result.get("metadatas", [])
            embeddings_list = result.get("embeddings", [])

            results: list[dict] = []
            for i, drawer_id in enumerate(ids_list):
                results.append(
                    {
                        "id": drawer_id,
                        "content": documents_list[i] if i < len(documents_list) else "",
                        "metadata": metadatas_list[i] if i < len(metadatas_list) else {},
                        "embedding": embeddings_list[i] if i < len(embeddings_list) else [],
                    }
                )
            return results
        except Exception as e:
            logger.warning("get_chunks 失败: %s", e)
            return []

    def delete_drawer(self, drawer_id: str) -> bool:
        """删除单条记录。"""
        if not self._available:
            return False
        try:
            self._collection.delete(ids=[drawer_id])
            return True
        except Exception as e:
            logger.warning("delete_drawer 失败: %s", e)
            return False

    def delete_drawers(self, drawer_ids: list[str]) -> bool:
        """批量删除。"""
        if not self._available or not drawer_ids:
            return False
        try:
            self._collection.delete(ids=drawer_ids)
            return True
        except Exception as e:
            logger.warning("delete_drawers 失败: %s", e)
            return False

    def delete_by_source(self, source_file: str) -> int:
        """按 source_file 删除所有相关记录，返回删除数量。"""
        if not self._available:
            return 0
        try:
            # 先获取所有相关 ID
            result = self._collection.get(
                where={"source_file": source_file},
                limit=100000,
                include=[],
            )
            ids_list = result.get("ids", [])
            if not ids_list:
                return 0
            self._collection.delete(ids=ids_list)
            return len(ids_list)
        except Exception as e:
            logger.warning("delete_by_source 失败: %s", e)
            return 0

    def count(self) -> int:
        """返回 collection 中的记录数。"""
        if not self._available:
            return 0
        try:
            return self._collection.count()
        except Exception as e:
            logger.warning("count 失败: %s", e)
            return 0

    def list_wings(self) -> list[tuple[str, int]]:
        """从所有记录的元数据中聚合 wing 字段。

        Returns:
            [(wing_name, count), ...]
        """
        if not self._available:
            return []
        try:
            result = self._collection.get(
                limit=100000,
                include=["metadatas"],
            )
            metadatas_list = result.get("metadatas", [])
            counts: dict[str, int] = {}
            for meta in metadatas_list:
                wing = meta.get("wing", "")
                if wing:
                    counts[wing] = counts.get(wing, 0) + 1
            return list(counts.items())
        except Exception as e:
            logger.warning("list_wings 失败: %s", e)
            return []

    def list_rooms(self, wing: str) -> list[tuple[str, int]]:
        """过滤 wing 后聚合 room。

        Returns:
            [(room_name, count), ...]
        """
        if not self._available:
            return []
        try:
            result = self._collection.get(
                where={"wing": wing},
                limit=100000,
                include=["metadatas"],
            )
            metadatas_list = result.get("metadatas", [])
            counts: dict[str, int] = {}
            for meta in metadatas_list:
                room = meta.get("room", "")
                if room:
                    counts[room] = counts.get(room, 0) + 1
            return list(counts.items())
        except Exception as e:
            logger.warning("list_rooms 失败: %s", e)
            return []

    def list_drawers_by_importance(
        self,
        limit: int = 15,
        wing: str | None = None,
    ) -> list[dict]:
        """按 importance 降序排序获取抽屉。

        Returns:
            [{"id": ..., "content": ..., "metadata": ...}, ...]
        """
        if not self._available:
            return []
        try:
            where = {"wing": wing} if wing else None
            result = self._collection.get(
                where=where,
                limit=100000,
                include=["documents", "metadatas"],
            )
            ids_list = result.get("ids", [])
            documents_list = result.get("documents", [])
            metadatas_list = result.get("metadatas", [])

            # 构建抽屉列表，带临时排序字段
            drawers: list[dict] = []
            for i, drawer_id in enumerate(ids_list):
                meta = metadatas_list[i] if i < len(metadatas_list) else {}
                drawers.append(
                    {
                        "id": drawer_id,
                        "content": documents_list[i] if i < len(documents_list) else "",
                        "metadata": meta,
                        "_importance": meta.get("importance", 0.5),
                    }
                )
            # 按 importance 降序排序，取 top limit
            drawers.sort(key=lambda d: d["_importance"], reverse=True)
            top_drawers = drawers[:limit]
            # 移除临时排序字段
            for d in top_drawers:
                d.pop("_importance", None)
            return top_drawers
        except Exception as e:
            logger.warning("list_drawers_by_importance 失败: %s", e)
            return []
