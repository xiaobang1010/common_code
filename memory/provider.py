"""MemoryPalaceProvider - MemoryProvider 协议适配层。

将 Palace 核心功能适配为 MemoryProvider 协议（store/retrieve/search/clear），
同时暴露扩展 API（wake_up, recall, add_drawer, kg 操作等）供工具和循环调用。
"""

from __future__ import annotations

import logging
from pathlib import Path

from memory.palace import PalaceManager
from memory.layers import MemoryStack
from memory.knowledge_graph import KnowledgeGraph
from memory.searcher import HybridSearcher
from memory.miner import FileMiner, ConversationMiner
from memory.models import Drawer, KGTriple
from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)


class MemoryPalaceProvider:
    """MemoryPalace 记忆后端 - 实现 MemoryProvider 协议。

    兼容现有 MemoryProvider 协议（store/retrieve/search/clear），
    同时暴露扩展 API 供记忆工具和查询循环调用。

    扩展 API:
    - wake_up(wing) -> str: L0+L1 唤醒上下文
    - recall(wing, room) -> str: L2 按需检索
    - search_memory(query, wing, room, limit) -> list[dict]: 混合搜索
    - add_drawer(wing, room, content, ...) -> dict: 写入 Drawer
    - get_drawer(drawer_id) -> dict | None
    - get_drawers_by_source(source_file) -> list[dict]
    - delete_drawer(drawer_id) -> bool
    - delete_by_source(source_file) -> int
    - list_wings() -> list[dict]
    - list_rooms(wing) -> list[dict]
    - get_taxonomy() -> dict
    - status() -> dict
    - kg_add(subject, predicate, object, ...) -> dict
    - kg_query(entity, as_of) -> list[dict]
    - kg_timeline(entity) -> list[dict]
    - kg_invalidate(subject, predicate, object, ended) -> int
    - kg_entities() -> list[str]
    - kg_supersede(subject, predicate, old_object, new_object, at) -> dict
    - repair_index() -> dict
    - cleanup_orphans() -> dict
    - mine_file(path, wing) -> int
    - mine_directory(path, wing) -> dict
    """

    def __init__(self, storage: PalaceStorage | None = None):
        if storage is None:
            storage = PalaceStorage()
            storage.init_schema()
        self.storage = storage
        self.palace = PalaceManager(storage)
        self.memory_stack = MemoryStack(storage)
        self.kg = KnowledgeGraph(storage)
        self.closet_indexer = self.palace.closet_indexer
        self.searcher = HybridSearcher(storage, self.closet_indexer)
        self.file_miner = FileMiner(self.palace)
        self.convo_miner = ConversationMiner(self.palace)

    # --- MemoryProvider 协议实现 (async) ---

    async def store(self, session_id: str, key: str, content: str) -> None:
        """存储一条记忆。

        将内容作为 Drawer 写入 Palace：
        - wing = session_id
        - room = key
        - source_file = "memory_provider"
        """
        self.palace.add_drawer(
            wing=session_id, room=key, content=content,
            source_file="memory_provider",
        )

    async def retrieve(self, session_id: str, key: str) -> str | None:
        """检索一条记忆。

        按 wing=session_id, room=key 检索最新的 Drawer。
        返回 content，不存在返回 None。
        """
        drawers = self.storage.list_drawers(
            wing=session_id, room=key, limit=1
        )
        if drawers:
            return drawers[0].content
        return None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索相关历史记忆。

        调用 HybridSearcher，返回 [{"session_id": ..., "key": ..., "content": ..., "score": ...}, ...]
        """
        results = self.searcher.search_raw(query, limit=limit)
        return [
            {
                "session_id": r["drawer"].wing,
                "key": r["drawer"].room,
                "content": r["drawer"].content,
                "score": r["score"],
            }
            for r in results
        ]

    async def clear(self, session_id: str) -> None:
        """清除指定会话的所有记忆。

        删除 wing=session_id 的所有 Drawer。
        """
        drawers = self.storage.list_drawers(wing=session_id, limit=100000)
        for d in drawers:
            self.palace.delete_drawer(d.id)

    # --- 扩展 API (sync) ---

    def wake_up(self, wing: str | None = None) -> str:
        """L0+L1 唤醒上下文。"""
        return self.memory_stack.wake_up(wing)

    def recall(self, wing: str | None = None, room: str | None = None) -> str:
        """L2 按需检索。"""
        return self.memory_stack.recall(wing, room)

    def search_memory(self, query: str, wing: str | None = None,
                      room: str | None = None, source_file: str | None = None,
                      limit: int = 5) -> list[dict]:
        """混合搜索，返回结构化结果。

        Returns:
            [{"drawer_id": ..., "content": ..., "score": ..., "bm25_score": ...,
              "vector_score": ..., "effective_distance": ..., "closet_boost": ...,
              "wing": ..., "room": ..., "source_file": ..., "filed_at": ...}, ...]
        """
        results = self.searcher.search(
            query, wing=wing, room=room, source_file=source_file, limit=limit
        )
        return [
            {
                "drawer_id": r.drawer.id,
                "content": r.drawer.content,
                "score": r.score,
                "bm25_score": r.bm25_score,
                "vector_score": r.vector_score,
                "effective_distance": 1.0 - r.vector_score,
                "closet_boost": r.closet_boost,
                "wing": r.drawer.wing,
                "room": r.drawer.room,
                "source_file": r.drawer.source_file,
                "filed_at": r.drawer.filed_at,
            }
            for r in results
        ]

    def add_drawer(self, wing: str, room: str, content: str,
                   source_file: str = "", importance: float = 0.5,
                   authored_at: str = "", chunk_index: int = 0,
                   source_mtime: float | None = None) -> dict:
        """写入 Drawer，返回 drawer 信息 dict。"""
        drawer = self.palace.add_drawer(
            wing=wing, room=room, content=content,
            source_file=source_file, importance=importance,
            authored_at=authored_at, chunk_index=chunk_index,
            source_mtime=source_mtime,
        )
        return _drawer_to_dict(drawer)

    def get_drawer(self, drawer_id: str) -> dict | None:
        """按 ID 获取 Drawer，返回 dict 或 None。"""
        drawer = self.palace.get_drawer(drawer_id)
        return _drawer_to_dict(drawer) if drawer else None

    def get_drawers_by_source(self, source_file: str) -> list[dict]:
        """获取指定来源文件的所有 Drawer。"""
        drawers = self.palace.get_drawers_by_source(source_file)
        return [_drawer_to_dict(d) for d in drawers]

    def delete_drawer(self, drawer_id: str) -> bool:
        """删除 Drawer。"""
        return self.palace.delete_drawer(drawer_id)

    def delete_by_source(self, source_file: str) -> int:
        """删除指定来源文件的所有 Drawer。"""
        return self.palace.delete_by_source(source_file)

    def list_wings(self) -> list[dict]:
        """列出所有 Wing。"""
        return self.palace.list_wings()

    def list_rooms(self, wing: str) -> list[dict]:
        """列出指定 Wing 下的 Room。"""
        return self.palace.list_rooms(wing)

    def get_taxonomy(self) -> dict:
        """获取分类树。"""
        return self.palace.get_taxonomy()

    def get_status(self) -> dict:
        """获取 Palace 状态。"""
        return self.palace.status()

    # --- 知识图谱 ---

    def kg_add(self, subject: str, predicate: str, object: str,
               valid_from: str | None = None, drawer_refs: str = "") -> dict:
        """添加三元组，返回 triple 信息 dict。"""
        triple = self.kg.add_triple(
            subject, predicate, object, valid_from, drawer_refs
        )
        return _triple_to_dict(triple)

    def kg_query(self, entity: str, as_of: str | None = None) -> list[dict]:
        """查询实体关系。"""
        triples = self.kg.query_entity(entity, as_of)
        return [_triple_to_dict(t) for t in triples]

    def kg_timeline(self, entity: str) -> list[dict]:
        """查询实体时间线。"""
        triples = self.kg.query_timeline(entity)
        return [_triple_to_dict(t) for t in triples]

    def kg_invalidate(self, subject: str, predicate: str, object: str,
                      ended: str | None = None) -> int:
        """使三元组失效。"""
        return self.kg.invalidate(subject, predicate, object, ended)

    def kg_entities(self) -> list[str]:
        """列出所有实体。"""
        return self.kg.list_entities()

    def kg_supersede(self, subject: str, predicate: str, old_object: str,
                     new_object: str, at: str | None = None) -> dict:
        """原子替换事实 - 关闭旧事实 + 打开新事实。

        Args:
            subject: 主体实体
            predicate: 关系类型
            old_object: 旧客体
            new_object: 新客体
            at: 边界时间（ISO 8601），默认当前时间

        Returns:
            {"invalidated": count, "added": triple_dict}
        """
        return self.kg.supersede(subject, predicate, old_object, new_object, at)

    def repair_index(self) -> dict:
        """重建 FTS5 索引。

        Returns:
            {"rebuilt": True, "drawer_count": N}
        """
        from memory.repair import repair_fts_index
        return repair_fts_index(self.storage)

    def cleanup_orphans(self) -> dict:
        """清理孤立记录（Closet 条目和 KG 三元组）。

        Returns:
            {"closets": {...}, "triples": {...}}
        """
        from memory.repair import cleanup_orphan_closets, cleanup_orphan_triples
        closets = cleanup_orphan_closets(self.storage)
        triples = cleanup_orphan_triples(self.storage)
        return {"closets": closets, "triples": triples}

    # --- 摄取 ---

    def mine_file(self, file_path: str, wing: str | None = None) -> int:
        """摄取文件。"""
        return self.file_miner.mine_file(Path(file_path), wing)

    def mine_directory(self, dir_path: str, wing: str | None = None) -> dict:
        """摄取目录。"""
        return self.file_miner.mine_directory(Path(dir_path), wing)


def _drawer_to_dict(drawer: Drawer) -> dict:
    """将 Drawer 对象转换为 dict。"""
    return {
        "id": drawer.id,
        "wing": drawer.wing,
        "room": drawer.room,
        "content": drawer.content,
        "source_file": drawer.source_file,
        "filed_at": drawer.filed_at,
        "authored_at": drawer.authored_at,
        "chunk_index": drawer.chunk_index,
        "importance": drawer.importance,
        "source_mtime": drawer.source_mtime,
        "content_hash": drawer.content_hash,
    }


def _triple_to_dict(triple: KGTriple) -> dict:
    """将 KGTriple 对象转换为 dict。"""
    return {
        "id": triple.id,
        "subject": triple.subject,
        "predicate": triple.predicate,
        "object": triple.object,
        "valid_from": triple.valid_from,
        "valid_to": triple.valid_to,
        "drawer_refs": triple.drawer_refs,
        "created_at": triple.created_at,
    }
