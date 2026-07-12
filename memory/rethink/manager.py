"""rethink 类 - 改记忆。

抽屉内容不可变，修改通过"删旧插新"或元数据更新完成。
支持三条路径：仅改元数据 / 改内容重新嵌入 / noop。
"""

from __future__ import annotations

import logging

from memory.palace.sanitize import sanitize_content

logger = logging.getLogger(__name__)


class RethinkManager:
    """改记忆管理器。

    抽屉内容一旦写入就不可变。修改记忆有两种方式：
    1. 只改元数据（wing/room）-- 直接更新 ChromaDB 的 metadata，不重新嵌入
    2. 改内容 -- 删掉旧抽屉（含分块），用新内容重新嵌入，生成新 ID

    Attributes:
        chroma_store: ChromaDB 向量存储
        remember_manager: 存记忆管理器（用于重新嵌入）
    """

    def __init__(self, chroma_store, remember_manager):
        self.chroma_store = chroma_store
        self.remember_manager = remember_manager

    def _get_existing_with_chunks(
        self, drawer_id: str
    ) -> tuple[dict | None, list[dict]]:
        """获取抽屉及其分块。

        先按 ID 直接查记录。不管找没找到，都顺带查一下分块
        （parent_drawer_id 等于 drawer_id 的记录）。如果主记录
        不存在但分块存在，调用方可以拿第一个分块当基础。

        Args:
            drawer_id: 抽屉 ID

        Returns:
            (existing_dict, chunks_list)
            existing_dict 为 None 表示该 ID 没有直接记录
        """
        existing = self.chroma_store.get_drawer(drawer_id)
        chunks = self.chroma_store.get_chunks(drawer_id)
        return existing, chunks

    def rethink(
        self,
        drawer_id: str,
        content: str | None = None,
        wing: str | None = None,
        room: str | None = None,
    ) -> str:
        """修改抽屉。

        三条路径：
        A. noop -- 什么都没传，或传的值跟现有的一样，直接返回原 ID
        B. 仅改元数据 -- 内容不变，只改 wing/room，更新 metadata 不重新嵌入
        C. 改内容 -- 删旧插新，用新内容重新嵌入，返回新 ID

        Args:
            drawer_id: 要改的抽屉 ID
            content: 新内容，None 表示不改内容
            wing: 新 wing，None 表示不改
            room: 新 room，None 表示不改

        Returns:
            抽屉 ID。路径 A/B 返回原 ID，路径 C 返回新 ID

        Raises:
            ValueError: 抽屉不存在且无分块
        """
        # 1. 拿到现有记录和分块
        existing, chunks = self._get_existing_with_chunks(drawer_id)

        # 主记录不存在时，退而求其次，拿第一个分块当基础
        if existing is None:
            if chunks:
                existing = chunks[0]
            else:
                raise ValueError(f"抽屉不存在: {drawer_id}")

        existing_metadata = existing.get("metadata", {})
        existing_content = existing.get("content", "")
        existing_embedding = existing.get("embedding", [])

        existing_wing = existing_metadata.get("wing", "")
        existing_room = existing_metadata.get("room", "")

        # 2. 路径 A: noop -- 什么都没传
        if content is None and wing is None and room is None:
            return drawer_id

        # 新内容先做 sanitize 再比较
        sanitized_content = (
            sanitize_content(content) if content is not None else None
        )

        # 确定新 wing/room：传了就用传的，没传保持原样
        new_wing = wing if wing is not None else existing_wing
        new_room = room if room is not None else existing_room

        # 内容有没有变：没传新内容，或新内容跟旧的一样
        content_unchanged = sanitized_content is None or sanitized_content == existing_content

        # 内容和元数据都没变 -> noop
        if content_unchanged and new_wing == existing_wing and new_room == existing_room:
            return drawer_id

        # 3. 路径 B: 仅改元数据
        # 内容不变，但 wing 或 room 变了，直接更新 metadata，不重新嵌入
        if content_unchanged:
            updated_metadata = dict(existing_metadata)
            updated_metadata["wing"] = new_wing
            updated_metadata["room"] = new_room
            self.chroma_store.upsert_drawer(
                drawer_id, existing_content, existing_embedding, updated_metadata
            )
            return drawer_id

        # 4. 路径 C: 改内容（删旧插新）
        # 先删掉旧抽屉及其分块
        if chunks:
            chunk_ids = [c["id"] for c in chunks]
            self.chroma_store.delete_drawers(chunk_ids)
        # 父 ID 本身也删掉（如果它也是一条独立记录的话）
        self.chroma_store.delete_drawer(drawer_id)

        # 从旧记录保留这些元数据
        source_file = existing_metadata.get("source_file", "")
        importance = existing_metadata.get("importance", 0.5)
        authored_at = existing_metadata.get("authored_at", "")
        chunk_index = existing_metadata.get("chunk_index", 0)
        source_mtime = existing_metadata.get("source_mtime", None)
        # ChromaDB 把 None 存成空字符串，这里转回去
        if source_mtime == "":
            source_mtime = None

        # 用新内容重新添加
        new_drawer = self.remember_manager.add_drawer(
            wing=new_wing,
            room=new_room,
            content=sanitized_content,
            source_file=source_file,
            importance=importance,
            authored_at=authored_at,
            chunk_index=chunk_index,
            source_mtime=source_mtime,
        )
        return new_drawer.id
