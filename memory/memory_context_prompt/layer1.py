"""L1 关键故事层 - 始终加载，约 500-800 tokens。

从 ChromaStore 按 importance 排序选取 Top-15 抽屉，
按 Room 分组，输出紧凑格式。硬上限 3200 字符。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Layer1:
    """L1 关键故事层 - 始终加载，约 500-800 tokens。

    从 ChromaStore 中按 importance 排序选取 Top-15 抽屉，
    按 Room 分组，输出紧凑格式。硬上限 3200 字符。
    """

    MAX_DRAWERS = 15   # 最多 15 条记忆
    MAX_CHARS = 3200    # L1 文本硬上限
    MAX_SCAN = 2000     # 扫描上限（从存储获取的最大候选数）

    def __init__(self, chroma_store):
        self.chroma_store = chroma_store

    def generate(self, wing: str | None = None) -> str:
        """生成 L1 关键故事文本。

        步骤：
        1. 从 chroma_store.list_drawers_by_importance 获取 Top-15 抽屉
        2. 按 metadata 中的 room 分组
        3. 每个抽屉输出：[room] content[:200]...
        4. 总文本不超过 MAX_CHARS (3200)

        格式示例：
        ## Key Memories

        ### auth
        - [drawer_id] The login system uses JWT tokens...
        - [drawer_id] Rate limiting was added to prevent...

        ### database
        - [drawer_id] PostgreSQL connection pool configured...
        """
        drawers = self.chroma_store.list_drawers_by_importance(
            limit=self.MAX_SCAN, wing=wing
        )
        # 取 Top-N
        drawers = drawers[: self.MAX_DRAWERS]

        # 按 Room 分组（保持插入顺序）
        # 抽屉格式: {"id":..., "content":..., "metadata": {"wing":..., "room":...}}
        rooms: dict[str, list[dict]] = {}
        for d in drawers:
            metadata = d.get("metadata", {})
            room = metadata.get("room", "")
            rooms.setdefault(room, []).append(d)

        # 构建输出，逐条检查字符上限
        parts: list[str] = ["## Key Memories\n"]
        total = len(parts[0])

        for room, room_drawers in rooms.items():
            header = f"\n### {room}\n"
            if total + len(header) > self.MAX_CHARS:
                break
            parts.append(header)
            total += len(header)

            for d in room_drawers:
                content = d.get("content", "")
                content_preview = content[:200]
                if len(content) > 200:
                    content_preview += "..."
                line = f"- [{d.get('id', '')}] {content_preview}\n"
                if total + len(line) > self.MAX_CHARS:
                    return "".join(parts)
                parts.append(line)
                total += len(line)

        return "".join(parts)
