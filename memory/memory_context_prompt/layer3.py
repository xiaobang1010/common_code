"""L3 深度搜索层 - 按需触发，不限深度。

通过 PalaceManager 的 recall 方法进行混合搜索
（向量召回 + BM25 重排 + Closet boost）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Layer3:
    """L3 深度搜索层 - 按需触发，不限深度。

    通过 PalaceManager.recall 进行混合搜索
    （BM25 + 可选向量 + Closet boost）。
    """

    def __init__(self, palace_manager):
        self.palace_manager = palace_manager

    def search(self, query: str, wing: str | None = None,
               room: str | None = None, n_results: int = 5) -> str:
        """深度语义搜索。

        调用 PalaceManager.recall()，格式化结果。

        格式：
        ## Search Results for "query"

        ### Result 1 (score: 0.85)
        Source: login.py | Wing: my_project | Room: auth
        ---
        [verbatim content, up to 500 chars]

        ### Result 2 (score: 0.72)
        ...
        """
        results = self.palace_manager.recall(
            query, wing=wing, room=room, n_results=n_results
        )

        parts: list[str] = [f'## Search Results for "{query}"\n']

        if not results:
            parts.append("\nNo results found.\n")
            return "".join(parts)

        for i, result in enumerate(results, 1):
            content = result.get("content", "")
            content_preview = content[:500]
            score = result.get("score", 0.0)
            block = (
                f"\n### Result {i} (score: {score:.2f})\n"
                f"Source: {result.get('source_file', '')} | "
                f"Wing: {result.get('wing', '')} | "
                f"Room: {result.get('room', '')}\n"
                f"---\n"
                f"{content_preview}\n"
            )
            parts.append(block)

        return "".join(parts)

    def search_raw(self, query: str, wing: str | None = None,
                   room: str | None = None, n_results: int = 5) -> list[dict]:
        """返回结构化 dict 列表。

        每个 dict: {drawer_id, content, score, wing, room, source_file}
        """
        results = self.palace_manager.recall(
            query, wing=wing, room=room, n_results=n_results
        )
        return [
            {
                "drawer_id": r.get("drawer_id", ""),
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
                "wing": r.get("wing", ""),
                "room": r.get("room", ""),
                "source_file": r.get("source_file", ""),
            }
            for r in results
        ]
