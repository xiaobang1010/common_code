"""remember 类 - 存记忆。

管理抽屉的写入，支持单块和分块路径。
内容寻址：相同 wing+room+content 生成相同 ID，重复写入直接返回（幂等）。
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Any

from memory.palace.ids import content_hash, generate_drawer_id
from memory.palace.models import Drawer
from memory.palace.sanitize import sanitize_content

logger = logging.getLogger(__name__)

# 分块阈值：超过此字符数走分块路径
_CHUNK_THRESHOLD = 800


class RememberManager:
    """存记忆管理器。

    负责将抽屉写入 ChromaDB 向量存储，支持单块和分块两种路径。
    内容寻址机制保证相同内容幂等写入。

    Attributes:
        chroma_store: ChromaDB 向量存储
        embedding_provider: Jasper embedding 提供器
        closet_indexer: Closet 索引器
    """

    def __init__(
        self,
        chroma_store: Any,
        embedding_provider: Any = None,
        closet_indexer: Any = None,
    ):
        self.chroma_store = chroma_store
        self.embedding_provider = embedding_provider
        self.closet_indexer = closet_indexer

    def add_drawer(
        self,
        wing: str,
        room: str,
        content: str,
        source_file: str = "",
        importance: float = 0.5,
        authored_at: str = "",
        chunk_index: int = 0,
        source_mtime: float | None = None,
    ) -> Drawer:
        """存入一个抽屉，自动判断是否需要分块。

        内容超过 800 字符时走分块路径，否则走单块路径。
        相同 wing+room+content 会生成相同 ID，重复写入直接返回（幂等）。

        Args:
            wing: 顶层命名空间（project/person/domain）
            room: wing 内的子分类
            content: 原始文本内容
            source_file: 来源文件路径
            importance: 重要性评分（0.0-1.0）
            authored_at: 内容原始创建时间（ISO 8601）
            chunk_index: 来源文件内的分块索引（0-based）
            source_mtime: 来源文件的 mtime

        Returns:
            写入的 Drawer 对象（分块路径下返回父 Drawer）
        """
        # 第一步：清理有害字符
        content = sanitize_content(content)
        if not content:
            logger.warning("清理后内容为空，跳过写入: wing=%s room=%s", wing, room)
            return Drawer(
                id="",
                wing=wing,
                room=room,
                content="",
            )

        # 第二步：根据长度选择路径
        if len(content) <= _CHUNK_THRESHOLD:
            return self._add_single_drawer(
                wing=wing,
                room=room,
                content=content,
                source_file=source_file,
                importance=importance,
                authored_at=authored_at,
                chunk_index=chunk_index,
                source_mtime=source_mtime,
            )
        else:
            return self._add_chunked_drawer(
                wing=wing,
                room=room,
                content=content,
                source_file=source_file,
                importance=importance,
                authored_at=authored_at,
                source_mtime=source_mtime,
            )

    def _add_single_drawer(
        self,
        wing: str,
        room: str,
        content: str,
        source_file: str,
        importance: float,
        authored_at: str,
        chunk_index: int,
        source_mtime: float | None,
    ) -> Drawer:
        """单块写入路径：内容 <= 800 字符。

        内容寻址生成 ID，幂等检查后写入 ChromaDB 和 Closet 索引。
        """
        drawer_id = generate_drawer_id(wing, room, content)
        c_hash = content_hash(content)

        # 幂等检查：已存在则直接返回 Drawer 对象
        existing = self.chroma_store.get_drawer(drawer_id)
        if existing is not None:
            logger.debug("抽屉已存在，幂等返回: %s", drawer_id)
            return Drawer(
                id=drawer_id,
                wing=wing,
                room=room,
                content=content,
                source_file=source_file,
                filed_at=existing.get("metadata", {}).get("filed_at", ""),
                authored_at=authored_at,
                chunk_index=chunk_index,
                importance=importance,
                source_mtime=source_mtime,
                content_hash=c_hash,
                parent_drawer_id="",
            )

        filed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 构建 Drawer 对象
        drawer = Drawer(
            id=drawer_id,
            wing=wing,
            room=room,
            content=content,
            source_file=source_file,
            filed_at=filed_at,
            authored_at=authored_at,
            chunk_index=chunk_index,
            importance=importance,
            source_mtime=source_mtime,
            content_hash=c_hash,
            parent_drawer_id="",
        )

        # 嵌入向量（不可用时传 None，仍存储文档）
        vec: list[float] | None = None
        if self.embedding_provider is not None and self.embedding_provider.available:
            vec = self.embedding_provider.embed(content)

        # 构建 metadata（None 值转为空字符串，ChromaDB 限制）
        metadata = self._build_metadata(
            wing=wing,
            room=room,
            source_file=source_file,
            filed_at=filed_at,
            authored_at=authored_at,
            chunk_index=chunk_index,
            importance=importance,
            source_mtime=source_mtime,
            content_hash=c_hash,
            parent_drawer_id="",
        )

        self.chroma_store.upsert_drawer(drawer_id, content, vec, metadata)

        # Closet 索引（可用时）
        if self.closet_indexer is not None:
            try:
                self.closet_indexer.index_drawer(drawer)
            except Exception as e:
                logger.warning("Closet 索引失败: %s", e)

        return drawer

    def _add_chunked_drawer(
        self,
        wing: str,
        room: str,
        content: str,
        source_file: str,
        importance: float,
        authored_at: str,
        source_mtime: float | None,
    ) -> Drawer:
        """分块写入路径：内容 > 800 字符。

        将长文本切分为多块，每块独立嵌入和存储，
        通过 parent_drawer_id 关联到父抽屉。
        返回父 Drawer 对象（用前 200 字符构建）。
        """
        chunks = self._chunk_content(content)
        filed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 父 ID 用内容前 200 字符生成，避免整篇内容哈希
        parent_drawer_id = self._generate_parent_id(wing, room, content)

        # 批量嵌入
        embeddings: list[list[float] | None] = [None] * len(chunks)
        if self.embedding_provider is not None and self.embedding_provider.available:
            embeddings = self.embedding_provider.embed_batch(chunks)

        ids: list[str] = []
        metadatas: list[dict] = []
        for index, chunk in enumerate(chunks):
            chunk_id = f"{parent_drawer_id}_chunk_{index}"
            chunk_hash = content_hash(chunk)
            ids.append(chunk_id)
            metadatas.append(
                self._build_metadata(
                    wing=wing,
                    room=room,
                    source_file=source_file,
                    filed_at=filed_at,
                    authored_at=authored_at,
                    chunk_index=index,
                    importance=importance,
                    source_mtime=source_mtime,
                    content_hash=chunk_hash,
                    parent_drawer_id=parent_drawer_id,
                )
            )

        # 批量 upsert
        self.chroma_store.upsert_drawers(ids, chunks, embeddings, metadatas)

        # 构建父 Drawer 对象（用前 200 字符代表整篇内容）
        parent_content_preview = content[:200]
        parent_drawer = Drawer(
            id=parent_drawer_id,
            wing=wing,
            room=room,
            content=parent_content_preview,
            source_file=source_file,
            filed_at=filed_at,
            authored_at=authored_at,
            chunk_index=0,
            importance=importance,
            source_mtime=source_mtime,
            content_hash=content_hash(content),
            parent_drawer_id="",
        )

        # Closet 索引（对父 Drawer 建索引）
        if self.closet_indexer is not None:
            try:
                self.closet_indexer.index_drawer(parent_drawer)
            except Exception as e:
                logger.warning("Closet 索引失败: %s", e)

        logger.info(
            "分块写入完成: parent=%s, chunks=%d, wing=%s room=%s",
            parent_drawer_id,
            len(chunks),
            wing,
            room,
        )
        return parent_drawer

    def add_drawers(self, drawers_data: list[dict]) -> int:
        """批量存入抽屉，返回成功数量。

        Args:
            drawers_data: 抽屉数据字典列表，每个字典的键对应 add_drawer 的参数名

        Returns:
            成功写入的数量（分块写入算 1 个）
        """
        success = 0
        for data in drawers_data:
            try:
                self.add_drawer(
                    wing=data.get("wing", ""),
                    room=data.get("room", ""),
                    content=data.get("content", ""),
                    source_file=data.get("source_file", ""),
                    importance=data.get("importance", 0.5),
                    authored_at=data.get("authored_at", ""),
                    chunk_index=data.get("chunk_index", 0),
                    source_mtime=data.get("source_mtime"),
                )
                success += 1
            except Exception as e:
                logger.warning("批量写入第 %d 项失败: %s", success, e)
        return success

    def _chunk_content(
        self,
        content: str,
        chunk_size: int = 800,
        overlap: int = 100,
    ) -> list[str]:
        """将长文本切分为重叠的块。

        切分策略（优先级递减）：
        1. 优先在段落边界（\\n\\n）切分
        2. 其次在行边界（\\n）切分
        3. 最后按字符硬切分

        每块约 chunk_size 字符，相邻块有 overlap 字符重叠。
        最后一块若 < 100 字符则合并到前一块。

        Args:
            content: 原始文本
            chunk_size: 目标块大小（字符）
            overlap: 重叠字符数

        Returns:
            切分后的文本块列表
        """
        if not content:
            return []

        if len(content) <= chunk_size:
            return [content]

        chunks: list[str] = []
        start = 0

        while start < len(content):
            end = start + chunk_size

            if end >= len(content):
                # 最后一块，直接取剩余
                remaining = content[start:]
                if remaining:
                    chunks.append(remaining)
                break

            # 尝试在边界处切分，避免硬截断
            cut = self._find_cut_position(content, start, end, chunk_size)

            chunk = content[start:cut]
            if chunk:
                chunks.append(chunk)

            # 下一块从 cut - overlap 开始，保证重叠
            next_start = cut - overlap
            if next_start <= start:
                # 防止死循环：至少前进 1
                next_start = start + 1
            start = next_start

        # 最后一块过小则合并到前一块
        if len(chunks) >= 2 and len(chunks[-1]) < overlap:
            chunks[-2] = chunks[-2] + chunks[-1]
            chunks.pop()

        return chunks

    def _find_cut_position(
        self,
        content: str,
        start: int,
        end: int,
        chunk_size: int,
    ) -> int:
        """在 [start, end] 范围内寻找最佳切分位置。

        优先级：段落边界 > 行边界 > 硬切分。
        搜索范围：end 附近前后 chunk_size/4 的区间。

        Returns:
            切分位置（content 索引）
        """
        # 搜索窗口：end 前后各 chunk_size/4，但不超过内容范围
        search_back = max(start, end - chunk_size // 4)
        search_forward = min(len(content), end + chunk_size // 4)

        # 优先段落边界（\n\n）
        window = content[search_back:search_forward]
        rel = window.rfind("\n\n")
        if rel != -1:
            return search_back + rel + 2  # 切在 \n\n 之后

        # 其次行边界（\n）
        rel = window.rfind("\n")
        if rel != -1:
            return search_back + rel + 1  # 切在 \n 之后

        # 都找不到就硬切分
        return end

    def _build_metadata(
        self,
        wing: str,
        room: str,
        source_file: str,
        filed_at: str,
        authored_at: str,
        chunk_index: int,
        importance: float,
        source_mtime: float | None,
        content_hash: str,
        parent_drawer_id: str,
    ) -> dict:
        """构建 ChromaDB 元数据字典，None 值转为空字符串。

        ChromaDB 元数据值只能是 str、int、float、bool，不接受 None。
        """
        return {
            "wing": wing,
            "room": room,
            "source_file": source_file,
            "filed_at": filed_at,
            "authored_at": authored_at,
            "chunk_index": chunk_index,
            "importance": importance,
            "source_mtime": source_mtime if source_mtime is not None else "",
            "content_hash": content_hash,
            "parent_drawer_id": parent_drawer_id,
        }

    @staticmethod
    def _generate_parent_id(wing: str, room: str, content: str) -> str:
        """生成分块父抽屉 ID。

        用内容前 200 字符生成哈希，避免整篇内容哈希导致 ID 过长。
        格式：drawer_{sha256(wing|room|content[:200])[:16]}
        """
        raw = f"{wing}|{room}|{content[:200]}"
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"drawer_{h}"
