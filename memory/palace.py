"""Palace 管理器 - 核心操作：增删改查 Drawer，构建索引，管理 Wing/Room。

Palace 隐喻：
  Palace -> Wing（翼，项目/人/领域）-> Room（房间，主题）-> Drawer（抽屉，记忆片段）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.closet import ClosetIndexer
from memory.collision_scan import assert_no_collisions
from memory.embedding import EmbeddingProvider, vector_to_bytes
from memory.models import Drawer, content_hash, generate_drawer_id
from memory.sanitize import sanitize_content
from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)


class PalaceManager:
    """Palace 核心管理器。

    管理 Drawer 的增删改查，自动构建 Closet 索引，
    提供 Wing/Room/Drawer 的导航和状态查询。

    Attributes:
        storage: 存储后端
        closet_indexer: Closet 索引器
    """

    def __init__(
        self,
        storage: PalaceStorage | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        if storage is None:
            storage = PalaceStorage()
            storage.init_schema()
        self.storage = storage
        self.closet_indexer = ClosetIndexer(storage)
        self.embedding_provider = embedding_provider

    # --- Drawer 写入 ---

    def add_drawer(self, wing: str, room: str, content: str,
                   source_file: str = "", importance: float = 0.5,
                   authored_at: str = "", chunk_index: int = 0,
                   source_mtime: float | None = None) -> Drawer:
        """添加抽屉。

        1. 生成 drawer_id (generate_drawer_id)
        2. 计算 content_hash (content_hash)
        3. 检查内容是否已存在 (check_content_exists) - 跳过重复
        4. 设置 filed_at 为当前 UTC 时间
        5. 创建 Drawer 对象
        6. 调用 storage.add_drawer
        7. 如果插入成功，构建 Closet 索引 (closet_indexer.index_drawer)
        8. 如果 embedding 可用，生成并存储向量嵌入
        9. 返回 Drawer 对象

        Args:
            wing: 顶层命名空间
            room: 子分类
            content: 逐字文本内容
            source_file: 来源文件路径
            importance: 重要性评分 (0.0-1.0)
            authored_at: 内容原始创建时间 (ISO 8601)
            chunk_index: 分块索引
            source_mtime: 来源文件 mtime

        Returns:
            创建的 Drawer 对象
        """
        content = sanitize_content(content)
        drawer_id = generate_drawer_id(wing, room, content)
        c_hash = content_hash(content)

        # 写入前碰撞检测：检查 Drawer ID 是否已存在
        assert_no_collisions([drawer_id], self.storage)

        if self.storage.check_content_exists(c_hash):
            logger.debug("内容已存在，跳过: %s", drawer_id)
            return Drawer(
                id=drawer_id, wing=wing, room=room, content=content,
                source_file=source_file, authored_at=authored_at,
                chunk_index=chunk_index, importance=importance,
                source_mtime=source_mtime, content_hash=c_hash,
            )

        filed_at = datetime.now(timezone.utc).isoformat()
        drawer = Drawer(
            id=drawer_id, wing=wing, room=room, content=content,
            source_file=source_file, filed_at=filed_at,
            authored_at=authored_at, chunk_index=chunk_index,
            importance=importance, source_mtime=source_mtime,
            content_hash=c_hash,
        )
        inserted = self.storage.add_drawer(drawer)
        if inserted:
            self.closet_indexer.index_drawer(drawer)
            # 生成并存储向量嵌入
            if self.embedding_provider and self.embedding_provider.available:
                vec = self.embedding_provider.embed(content)
                if vec:
                    self.storage.update_embedding(drawer.id, vector_to_bytes(vec))
        return drawer

    def add_drawers(self, drawers_data: list[dict]) -> int:
        """批量添加抽屉。

        Args:
            drawers_data: 字典列表，每个字典含 wing, room, content, source_file, importance 等键

        Returns:
            成功添加的数量
        """
        # 写入前碰撞检测
        assert_no_collisions(
            [d["wing"] + d["room"] + d["content"] for d in drawers_data],
            self.storage,
        )
        count = 0
        for data in drawers_data:
            content = data.get("content", "")
            c_hash = content_hash(content)
            if self.storage.check_content_exists(c_hash):
                continue
            self.add_drawer(
                wing=data.get("wing", ""),
                room=data.get("room", ""),
                content=content,
                source_file=data.get("source_file", ""),
                importance=data.get("importance", 0.5),
                authored_at=data.get("authored_at", ""),
                chunk_index=data.get("chunk_index", 0),
                source_mtime=data.get("source_mtime"),
            )
            count += 1
        return count

    # --- Drawer 读取 ---

    def get_drawer(self, drawer_id: str) -> Drawer | None:
        """按 ID 获取抽屉。"""
        return self.storage.get_drawer(drawer_id)

    def get_drawers_by_source(self, source_file: str) -> list[Drawer]:
        """获取指定来源文件的所有抽屉。"""
        return self.storage.list_drawers(source_file=source_file, limit=100000)

    # --- Drawer 删除 ---

    def delete_drawer(self, drawer_id: str) -> bool:
        """删除抽屉。"""
        return self.storage.delete_drawer(drawer_id)

    def delete_by_source(self, source_file: str) -> int:
        """删除指定来源文件的所有抽屉 + Closet 条目。

        1. 获取该 source_file 的所有 Drawer
        2. 删除 Closet 条目 (closet_indexer.remove_by_source)
        3. 删除 Drawer (storage.delete_by_source)
        4. 返回删除数量
        """
        self.closet_indexer.remove_by_source(source_file)
        return self.storage.delete_by_source(source_file)

    # --- 导航 ---

    def list_wings(self) -> list[dict]:
        """列出所有 Wing 及其抽屉数量。

        Returns:
            [{"name": wing_name, "drawer_count": count}, ...]
        """
        return [
            {"name": name, "drawer_count": count}
            for name, count in self.storage.list_wings()
        ]

    def list_rooms(self, wing: str) -> list[dict]:
        """列出指定 Wing 下的所有 Room 及其抽屉数量。

        Returns:
            [{"name": room_name, "drawer_count": count}, ...]
        """
        return [
            {"name": name, "drawer_count": count}
            for name, count in self.storage.list_rooms(wing)
        ]

    def get_taxonomy(self) -> dict:
        """获取完整的 Wing -> Room -> Count 分类树。

        Returns:
            {
                "wing1": {"room1": count, "room2": count},
                "wing2": {"room3": count},
            }
        """
        taxonomy: dict[str, dict[str, int]] = {}
        for wing_name, _ in self.storage.list_wings():
            rooms = self.storage.list_rooms(wing_name)
            taxonomy[wing_name] = {room_name: count for room_name, count in rooms}
        return taxonomy

    def status(self) -> dict:
        """获取 Palace 状态。

        Returns:
            {
                "total_drawers": int,
                "total_wings": int,
                "wings": [{"name": str, "drawer_count": int, "rooms": [...]}, ...]
            }
        """
        wings_raw = self.storage.list_wings()
        wings = []
        for wing_name, wing_count in wings_raw:
            rooms = [
                {"name": room_name, "drawer_count": room_count}
                for room_name, room_count in self.storage.list_rooms(wing_name)
            ]
            wings.append({
                "name": wing_name,
                "drawer_count": wing_count,
                "rooms": rooms,
            })
        return {
            "total_drawers": self.storage.count_drawers(),
            "total_wings": len(wings_raw),
            "wings": wings,
        }
