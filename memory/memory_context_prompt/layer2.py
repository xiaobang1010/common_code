"""L2 按需检索层 - 每次约 200-500 tokens。

按 wing/room 元数据过滤检索，不进行语义搜索。
通过 ChromaStore 获取后在内存中按 room 过滤。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Layer2:
    """L2 按需检索层 - 每次约 200-500 tokens。

    按 wing/room 元数据过滤检索，不进行语义搜索。
    从 ChromaStore 获取抽屉后在内存中按 room 过滤。
    """

    def __init__(self, chroma_store):
        self.chroma_store = chroma_store

    def retrieve(self, wing: str | None = None, room: str | None = None,
                 n_results: int = 10) -> str:
        """按 wing/room 过滤检索抽屉。

        从 chroma_store.list_drawers_by_importance 获取全部记录，
        在内存中按 room 过滤，取前 n_results 条。
        每条抽屉输出 content[:200]。

        格式：
        ## Recall: wing/room

        - [room] content[:200]...
        - ...
        """
        drawers = self.chroma_store.list_drawers_by_importance(
            limit=100000, wing=wing
        )

        # 在内存中按 room 过滤
        if room is not None:
            drawers = [
                d for d in drawers
                if d.get("metadata", {}).get("room") == room
            ]

        # 取前 n_results 条
        drawers = drawers[:n_results]

        wing_str = wing if wing is not None else "*"
        room_str = room if room is not None else "*"

        parts: list[str] = [f"## Recall: {wing_str}/{room_str}\n\n"]

        for d in drawers:
            content = d.get("content", "")
            content_preview = content[:200]
            if len(content) > 200:
                content_preview += "..."
            d_room = d.get("metadata", {}).get("room", "")
            parts.append(f"- [{d_room}] {content_preview}\n")

        return "".join(parts)
