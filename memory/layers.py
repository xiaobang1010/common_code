"""四层记忆栈 - 分层加载策略。

L0 Identity   (~100 tokens)   - 始终加载，身份信息
L1 Essential  (~500-800 tokens) - 始终加载，Top-N 关键故事
L2 On-Demand  (~200-500 tokens) - 按需触发，Wing/Room 过滤检索
L3 Deep       (无限)            - 按需触发，语义搜索
"""

from __future__ import annotations

import logging
from pathlib import Path

from memory.closet import ClosetIndexer
from memory.models import Drawer
from memory.searcher import HybridSearcher
from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer0 - 身份层
# ---------------------------------------------------------------------------


class Layer0:
    """L0 身份层 - 始终加载，约 100 tokens。

    从 ~/.agent/identity.txt 读取纯文本身份信息。
    内容示例：AI 助手名称、个性特征、关键人物、当前项目。
    """

    def __init__(self, identity_path: Path | None = None):
        if identity_path is None:
            identity_path = Path.home() / ".agent" / "identity.txt"
        self.identity_path = identity_path

    def render(self) -> str:
        """读取身份文件并返回文本。

        文件不存在时创建默认模板并返回。
        """
        if not self.identity_path.exists():
            # 创建默认身份模板
            default_content = """# AI Assistant Identity

## 基本信息
- 名称：AI 编程助手
- 项目：{project}

## 关键人物
（暂无记录）

## 当前项目
（暂无记录）
"""
            try:
                self.identity_path.parent.mkdir(parents=True, exist_ok=True)
                self.identity_path.write_text(default_content, encoding="utf-8")
                logger.info("创建默认身份模板: %s", self.identity_path)
            except Exception:
                pass
            return default_content

        try:
            return self.identity_path.read_text(encoding="utf-8")
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Layer1 - 关键故事层
# ---------------------------------------------------------------------------


class Layer1:
    """L1 关键故事层 - 始终加载，约 500-800 tokens。

    从 Palace 中按 importance 排序选取 Top-15 Drawer，
    按 Room 分组，输出紧凑格式。硬上限 3200 字符。
    """

    MAX_DRAWERS = 15   # 最多 15 条记忆
    MAX_CHARS = 3200    # L1 文本硬上限
    MAX_SCAN = 2000     # 扫描上限（从存储获取的最大候选数）

    def __init__(self, storage: PalaceStorage):
        self.storage = storage

    def generate(self, wing: str | None = None) -> str:
        """生成 L1 关键故事文本。

        步骤：
        1. 从 storage.list_drawers_by_importance 获取 Top-15 Drawer
        2. 按 Room 分组
        3. 每个 Drawer 输出：[room] content[:200]...
        4. 总文本不超过 MAX_CHARS (3200)

        格式示例：
        ## Key Memories

        ### auth
        - [drawer_id] The login system uses JWT tokens...
        - [drawer_id] Rate limiting was added to prevent...

        ### database
        - [drawer_id] PostgreSQL connection pool configured...
        """
        drawers = self.storage.list_drawers_by_importance(
            limit=self.MAX_SCAN, wing=wing
        )
        # 取 Top-N
        drawers = drawers[: self.MAX_DRAWERS]

        # 按 Room 分组（保持插入顺序）
        rooms: dict[str, list[Drawer]] = {}
        for d in drawers:
            rooms.setdefault(d.room, []).append(d)

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
                content_preview = d.content[:200]
                if len(d.content) > 200:
                    content_preview += "..."
                line = f"- [{d.id}] {content_preview}\n"
                if total + len(line) > self.MAX_CHARS:
                    return "".join(parts)
                parts.append(line)
                total += len(line)

        return "".join(parts)


# ---------------------------------------------------------------------------
# Layer2 - 按需检索层
# ---------------------------------------------------------------------------


class Layer2:
    """L2 按需检索层 - 每次约 200-500 tokens。

    按 wing/room 元数据过滤检索，不进行语义搜索。
    """

    def __init__(self, storage: PalaceStorage):
        self.storage = storage

    def retrieve(self, wing: str | None = None, room: str | None = None,
                 n_results: int = 10) -> str:
        """按 wing/room 过滤检索 Drawer。

        直接从 storage.list_drawers 获取，不调用搜索。
        每条 Drawer 输出 content[:200]，最多 n_results 条。

        格式：
        ## Recall: wing/room

        - [room] content[:200]...
        - ...
        """
        drawers = self.storage.list_drawers(
            wing=wing, room=room, limit=n_results
        )

        wing_str = wing if wing is not None else "*"
        room_str = room if room is not None else "*"

        parts: list[str] = [f"## Recall: {wing_str}/{room_str}\n\n"]

        for d in drawers:
            content_preview = d.content[:200]
            if len(d.content) > 200:
                content_preview += "..."
            parts.append(f"- [{d.room}] {content_preview}\n")

        return "".join(parts)


# ---------------------------------------------------------------------------
# Layer3 - 深度搜索层
# ---------------------------------------------------------------------------


class Layer3:
    """L3 深度搜索层 - 按需触发，不限深度。

    对全量 Palace 进行混合搜索（BM25 + 可选向量 + Closet boost）。
    """

    def __init__(self, searcher: HybridSearcher):
        self.searcher = searcher

    def search(self, query: str, wing: str | None = None,
               room: str | None = None, n_results: int = 5) -> str:
        """深度语义搜索。

        调用 HybridSearcher.search()，格式化结果。

        格式：
        ## Search Results for "query"

        ### Result 1 (score: 0.85)
        Source: login.py | Wing: my_project | Room: auth
        ---
        [verbatim content, up to 500 chars]

        ### Result 2 (score: 0.72)
        ...
        """
        results = self.searcher.search(
            query, wing=wing, room=room, limit=n_results
        )

        parts: list[str] = [f'## Search Results for "{query}"\n']

        if not results:
            parts.append("\nNo results found.\n")
            return "".join(parts)

        for i, result in enumerate(results, 1):
            d = result.drawer
            content_preview = d.content[:500]
            block = (
                f"\n### Result {i} (score: {result.score:.2f})\n"
                f"Source: {d.source_file} | Wing: {d.wing} | Room: {d.room}\n"
                f"---\n"
                f"{content_preview}\n"
            )
            parts.append(block)

        return "".join(parts)

    def search_raw(self, query: str, wing: str | None = None,
                   room: str | None = None, n_results: int = 5) -> list[dict]:
        """返回结构化 dict 列表。

        每个 dict: {drawer_id, content, score, wing, room, source_file, filed_at}
        """
        results = self.searcher.search(
            query, wing=wing, room=room, limit=n_results
        )
        return [
            {
                "drawer_id": r.drawer.id,
                "content": r.drawer.content,
                "score": r.score,
                "wing": r.drawer.wing,
                "room": r.drawer.room,
                "source_file": r.drawer.source_file,
                "filed_at": r.drawer.filed_at,
            }
            for r in results
        ]


# ---------------------------------------------------------------------------
# MemoryStack - 四层统一接口
# ---------------------------------------------------------------------------


class MemoryStack:
    """四层记忆栈统一接口。

    将 L0-L3 整合为单一入口，提供分层加载策略。
    """

    def __init__(self, storage: PalaceStorage | None = None,
                 identity_path: Path | None = None):
        if storage is None:
            storage = PalaceStorage()
            storage.init_schema()
        self.storage = storage

        closet_indexer = ClosetIndexer(storage)
        searcher = HybridSearcher(storage, closet_indexer)

        self.l0 = Layer0(identity_path)
        self.l1 = Layer1(storage)
        self.l2 = Layer2(storage)
        self.l3 = Layer3(searcher)

    def wake_up(self, wing: str | None = None) -> str:
        """唤醒 - L0 + L1，约 600-900 tokens。

        注入系统提示词，提供身份和关键故事。
        """
        parts: list[str] = []
        l0 = self.l0.render()
        if l0:
            parts.append(l0)
        l1 = self.l1.generate(wing)
        if l1:
            parts.append(l1)
        return "\n".join(parts)

    def recall(self, wing: str | None = None, room: str | None = None) -> str:
        """按需检索 - L2。"""
        return self.l2.retrieve(wing=wing, room=room)

    def search(self, query: str, wing: str | None = None,
               room: str | None = None) -> str:
        """深度搜索 - L3。"""
        return self.l3.search(query, wing=wing, room=room)

    def status(self) -> dict:
        """所有层的状态报告。

        Returns:
            {
                "l0_identity": "loaded" | "empty",
                "l1_drawers": count,
                "total_drawers": count,
                "wings": [(name, count), ...],
            }
        """
        l0_text = self.l0.render()
        total = self.storage.count_drawers()
        l1_count = min(total, self.l1.MAX_DRAWERS)

        return {
            "l0_identity": "loaded" if l0_text else "empty",
            "l1_drawers": l1_count,
            "total_drawers": total,
            "wings": self.storage.list_wings(),
        }
