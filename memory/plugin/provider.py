"""MemoryPalaceProvider - MemoryProvider 协议适配层。

将 Palace 核心功能适配为 MemoryProvider 协议（store/retrieve/search/clear），
同时暴露扩展 API（wake_up, recall, rethink, add_drawer, kg 操作等）供工具和循环调用。

通过 PalaceManager 协调者委托到四个 CRUD 模块（remember/recall/rethink/forget）。
知识图谱通过 memory.palace.knowledge_graph 管理（仍依赖 SQLite）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from memory.palace.manager import PalaceManager
from memory.palace.models import Drawer, KGTriple

logger = logging.getLogger(__name__)


class MemoryPalaceProvider:
    """MemoryPalace 记忆后端 - 实现 MemoryProvider 协议。

    通过 PalaceManager 协调者委托到四个 CRUD 模块。

    扩展 API:
    - wake_up(wing) -> str: L0+L1 唤醒上下文
    - recall(query, wing, room, n_results) -> list[dict]: 语义搜索
    - rethink(drawer_id, content, wing, room) -> str: 修改抽屉
    - search_memory(query, wing, room, source_file, limit) -> list[dict]: 混合搜索
    - add_drawer(wing, room, content, ...) -> dict: 写入 Drawer
    - get_drawer(drawer_id) -> dict | None
    - get_drawers_by_source(source_file) -> list[dict]
    - delete_drawer(drawer_id) -> bool
    - delete_by_source(source_file) -> int
    - list_wings() -> list[dict]
    - list_rooms(wing) -> list[dict]
    - get_taxonomy() -> dict
    - get_status() -> dict
    - kg_add(subject, predicate, object, ...) -> dict
    - kg_query(entity, as_of) -> list[dict]
    - kg_timeline(entity) -> list[dict]
    - kg_invalidate(subject, predicate, object, ended) -> int
    - kg_entities() -> list[str]
    - kg_supersede(subject, predicate, old_object, new_object, at) -> dict
    - mine_conversation(convo_messages, wing, session_id) -> int: 会话自动摄取
    """

    def __init__(self):
        # PalaceManager 协调者，内部管理 ChromaStore 和 embedding
        self.palace = PalaceManager()

        # 四层记忆栈
        self.memory_stack = None
        try:
            from memory.memory_context_prompt import MemoryContextPromptStack
            self.memory_stack = MemoryContextPromptStack(
                chroma_store=self.palace.chroma_store,
                palace_manager=self.palace,
            )
        except Exception as e:
            logger.warning("MemoryContextPromptStack 初始化失败: %s", e)

        # 知识图谱（依赖 SQLite，旧存储层保留）
        self.kg = None
        try:
            from memory.sqlite_store import PalaceStorage
            from memory.palace.knowledge_graph import KnowledgeGraph
            storage = PalaceStorage()
            storage.init_schema()
            self.kg = KnowledgeGraph(storage)
        except Exception as e:
            logger.warning("KnowledgeGraph 初始化失败: %s", e)

    # --- 记忆功能开关透传（feature API 使用）---

    def unload(self) -> None:
        """释放 embedding 模型内存（透传 PalaceManager 的 provider）。"""
        provider = self.palace.embedding_provider
        if provider is not None and hasattr(provider, "unload"):
            provider.unload()

    def embedding_status(self) -> dict:
        """只读状态快照 {loading, available}（透传，不触发加载）。"""
        provider = self.palace.embedding_provider
        if provider is None or not hasattr(provider, "status_snapshot"):
            return {"loading": False, "available": False}
        loading, available = provider.status_snapshot()
        return {"loading": loading, "available": available}

    def start_loading(self) -> None:
        """触发 embedding 模型后台加载并立即返回（透传 wait_loaded(timeout=0)）。"""
        provider = self.palace.embedding_provider
        if provider is not None and hasattr(provider, "wait_loaded"):
            provider.wait_loaded(timeout=0)

    # --- MemoryProvider 协议实现 (async) ---

    async def store(self, session_id: str, key: str, content: str) -> None:
        """存储一条记忆。"""
        self.palace.add_drawer(
            wing=session_id, room=key, content=content,
            source_file="memory_provider",
        )

    async def retrieve(self, session_id: str, key: str) -> str | None:
        """检索一条记忆。"""
        # 带上限拉取，避免 session 数据量大时全量扫描
        drawers = self.palace.list_drawers_by_importance(
            limit=2000, wing=session_id
        )
        for d in drawers:
            metadata = d.get("metadata", {})
            if metadata.get("room") == key:
                return d.get("content", "")
        return None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索相关历史记忆。"""
        results = self.palace.recall(query, n_results=limit)
        return [
            {
                "session_id": r.get("wing", ""),
                "key": r.get("room", ""),
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
            }
            for r in results
        ]

    async def clear(self, session_id: str) -> None:
        """清除指定会话的所有记忆。"""
        drawers = self.palace.list_drawers_by_importance(
            limit=100000, wing=session_id
        )
        ids = [d.get("id") for d in drawers if d.get("id")]
        if ids:
            self.palace.delete_drawers(ids)

    # --- 扩展 API (sync) ---

    def wake_up(self, wing: str | None = None) -> str:
        """L0+L1 唤醒上下文。"""
        if self.memory_stack is None:
            return ""
        return self.memory_stack.wake_up(wing)

    def recall(self, query: str, wing: str | None = None,
               room: str | None = None, n_results: int = 5) -> list[dict]:
        """语义搜索 - 委托到 PalaceManager。"""
        return self.palace.recall(
            query, wing=wing, room=room, n_results=n_results
        )

    def rethink(self, drawer_id: str, content: str | None = None,
                wing: str | None = None, room: str | None = None) -> str:
        """修改抽屉 - 委托到 PalaceManager。"""
        return self.palace.rethink(
            drawer_id, content=content, wing=wing, room=room
        )

    def search_memory(self, query: str, wing: str | None = None,
                      room: str | None = None, source_file: str | None = None,
                      limit: int = 5) -> list[dict]:
        """混合搜索，返回结构化结果。"""
        results = self.palace.recall(
            query, wing=wing, room=room, n_results=limit
        )
        if source_file is not None:
            results = [r for r in results if r.get("source_file") == source_file]
        return results

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
        """删除 Drawer，返回是否删除成功。"""
        count = self.palace.delete_drawer(drawer_id)
        return count > 0

    def delete_by_source(self, source_file: str) -> int:
        """删除指定来源文件的所有 Drawer，返回删除数量。"""
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

    # --- 自动摄取（会话结束） ---

    async def mine_conversation(
        self,
        convo_messages: list[dict],
        wing: str | None = None,
        session_id: str = "",
    ) -> int:
        """会话结束自动摄取：LLM 抽取候选记忆 → 置信度过滤 → 幂等写入。

        配置项（config.json 的 memory 分区）：
        - auto_mine: 总开关，默认开启
        - auto_mine_min_confidence: 置信度阈值，默认 0.7
        - auto_mine_max_items_per_session: 单会话最多写入条数，默认 5

        返回实际写入条数；配置关闭、LLM 不可用或抽取失败时返回 0，不抛异常。
        """
        try:
            from startup.config import get_global_config

            memory_cfg = get_global_config().memory or {}
        except Exception:
            memory_cfg = {}
        if not memory_cfg.get("auto_mine", True):
            return 0
        try:
            min_confidence = float(memory_cfg.get("auto_mine_min_confidence", 0.7))
            max_items = int(memory_cfg.get("auto_mine_max_items_per_session", 5))
        except (TypeError, ValueError):
            min_confidence, max_items = 0.7, 5

        # 惰性导入抽取器；失败由 miner 内部静默降级
        from memory.mine.miner import extract_candidates

        candidates = await extract_candidates(convo_messages)
        if not candidates:
            return 0

        # 按置信度过滤，取前 N 条
        picked = sorted(
            (c for c in candidates if c.get("confidence", 0.0) >= min_confidence),
            key=lambda c: c.get("confidence", 0.0),
            reverse=True,
        )[:max_items]

        wing = wing or "auto_mine"
        written = 0
        for c in picked:
            try:
                # add_drawer 内容寻址幂等：重复内容不会重复写入
                self.add_drawer(
                    wing=wing,
                    room=str(c["type"]),
                    content=str(c["content"]),
                    source_file="auto_mine",
                    importance=float(c.get("confidence", 0.5)),
                )
                written += 1
            except Exception as e:
                logger.warning("自动摄取写入失败: %s", e)
        return written

    # --- 知识图谱 ---

    def kg_add(self, subject: str, predicate: str, object: str,
               valid_from: str | None = None, drawer_refs: str = "") -> dict:
        """添加三元组，返回 triple 信息 dict。"""
        if self.kg is None:
            raise RuntimeError("KnowledgeGraph 不可用")
        triple = self.kg.add_triple(
            subject, predicate, object, valid_from, drawer_refs
        )
        return _triple_to_dict(triple)

    def kg_query(self, entity: str, as_of: str | None = None) -> list[dict]:
        """查询实体关系。"""
        if self.kg is None:
            raise RuntimeError("KnowledgeGraph 不可用")
        triples = self.kg.query_entity(entity, as_of)
        return [_triple_to_dict(t) for t in triples]

    def kg_timeline(self, entity: str) -> list[dict]:
        """查询实体时间线。"""
        if self.kg is None:
            raise RuntimeError("KnowledgeGraph 不可用")
        triples = self.kg.query_timeline(entity)
        return [_triple_to_dict(t) for t in triples]

    def kg_invalidate(self, subject: str, predicate: str, object: str,
                      ended: str | None = None) -> int:
        """使三元组失效。"""
        if self.kg is None:
            raise RuntimeError("KnowledgeGraph 不可用")
        return self.kg.invalidate(subject, predicate, object, ended)

    def kg_entities(self) -> list[str]:
        """列出所有实体。"""
        if self.kg is None:
            raise RuntimeError("KnowledgeGraph 不可用")
        return self.kg.list_entities()

    def kg_supersede(self, subject: str, predicate: str, old_object: str,
                     new_object: str, at: str | None = None) -> dict:
        """原子替换事实 - 关闭旧事实 + 打开新事实。"""
        if self.kg is None:
            raise RuntimeError("KnowledgeGraph 不可用")
        return self.kg.supersede(subject, predicate, old_object, new_object, at)


def _drawer_to_dict(drawer) -> dict:
    """将 Drawer 对象或 ChromaDB 文档 dict 转换为统一 dict。

    支持两种输入：
    - Drawer dataclass（来自 RememberManager.add_drawer）
    - ChromaDB 文档 dict（来自 RecallManager.get_drawer，格式 {"id":..., "content":..., "metadata":...}）
    """
    # Drawer dataclass 对象
    if isinstance(drawer, Drawer):
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
    # ChromaDB 文档 dict
    metadata = drawer.get("metadata", {})
    source_mtime = metadata.get("source_mtime", "")
    return {
        "id": drawer.get("id", ""),
        "wing": metadata.get("wing", ""),
        "room": metadata.get("room", ""),
        "content": drawer.get("content", ""),
        "source_file": metadata.get("source_file", ""),
        "filed_at": metadata.get("filed_at", ""),
        "authored_at": metadata.get("authored_at", ""),
        "chunk_index": metadata.get("chunk_index", 0),
        "importance": metadata.get("importance", 0.5),
        "source_mtime": float(source_mtime) if source_mtime else None,
        "content_hash": metadata.get("content_hash", ""),
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
