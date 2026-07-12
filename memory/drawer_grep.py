"""Drawer-Grep 上下文扩展 - 解决向量搜索落在错误 chunk 的问题。

当 Closet 命中某个 source_file 时，拉取该文件的所有 Drawer（chunk），
用查询词做关键词匹配找最佳 chunk，返回最佳 chunk + 前后邻居。
"""

from __future__ import annotations

import logging

from memory.models import Drawer
from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)

MAX_ENRICH_CHARS = 10000


class DrawerGrep:
    """Drawer-Grep 富化器。"""

    def __init__(self, storage: PalaceStorage):
        self.storage = storage

    def enrich(self, source_file: str, query_terms: list[str]) -> str | None:
        """富化搜索结果：找最佳 chunk + 前后邻居。

        Args:
            source_file: 来源文件路径
            query_terms: 查询词列表（已分词）

        Returns:
            拼接的上下文文本（最佳 chunk + 前后邻居），上限 10000 字符。
            无匹配时返回 None。
        """
        # 获取该来源文件的所有 Drawer，按 chunk_index 排序
        drawers = self.storage.list_drawers(source_file=source_file, limit=1000)
        if not drawers:
            return None

        drawers.sort(key=lambda d: d.chunk_index)

        # 找最佳匹配 chunk
        best_idx = -1
        best_score = 0

        for i, drawer in enumerate(drawers):
            content_lower = drawer.content.lower()
            score = sum(1 for term in query_terms if term.lower() in content_lower)
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx == -1 or best_score == 0:
            # 无关键词匹配，返回第一个 drawer
            best_idx = 0

        # 收集最佳 chunk + 前后邻居（±1）
        start_idx = max(0, best_idx - 1)
        end_idx = min(len(drawers), best_idx + 2)  # +2 因为切片是 exclusive

        parts = drawers[start_idx:end_idx]
        result = "\n\n".join(d.content for d in parts)

        # 截断到上限
        if len(result) > MAX_ENRICH_CHARS:
            result = result[:MAX_ENRICH_CHARS]

        return result
