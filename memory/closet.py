"""记忆宫殿壁橱索引层 - Closet 搜索加速。

壁橱（Closet）是对抽屉（Drawer）的二级索引：从抽屉内容中提取主题、实体、
标题和日期行号信息，构建紧凑的索引条目，用于加速混合检索。

  - ClosetExtractor：从 Drawer 内容中提取主题、实体、标题、日期行号
  - ClosetIndexer：管理壁橱条目的构建、存储、删除和 boost 查询
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from collections import Counter
from pathlib import Path

from memory.embedding import EmbeddingProvider, bytes_to_vector, vector_to_bytes
from memory.models import ClosetEntry, Drawer, content_hash
from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 英文动作动词（过去式）
_EN_VERBS = (
    "fixed",
    "added",
    "removed",
    "updated",
    "created",
    "deleted",
    "implemented",
    "refactored",
)

# 中文动作短语
_ZH_VERBS = ("修复了", "添加了", "删除了", "更新了", "创建了", "实现了")

# 需要过滤的编程关键字（大写形式，因实体正则仅匹配首字母大写词）
_PROGRAMMING_KEYWORDS = frozenset({
    "True",
    "False",
    "None",
    "Self",
    "Cls",
    "Def",
    "Class",
    "Import",
    "From",
    "Return",
    "Raise",
    "Try",
    "Except",
    "Finally",
    "With",
    "For",
    "While",
    "Yield",
    "Pass",
    "Break",
    "Continue",
    "Global",
    "Nonlocal",
    "Assert",
    "Lambda",
    "Async",
    "Await",
})

# 实体过滤停用词表（英文常见词，非专有名词）
_ENTITY_STOPLIST = frozenset({
    "The", "This", "That", "These", "Those",
    "After", "Before", "During", "While", "Since",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    "However", "Therefore", "Moreover", "Furthermore", "Nevertheless",
    "Because", "Although", "Unless", "Until", "Whether",
})

# Closet 条目目标大小（字符）
_CLOSET_TARGET_SIZE = 1500

# Closet boost 分数表
_BOOST_TOPIC_EXACT = 0.40
_BOOST_ENTITY_EXACT = 0.25
_BOOST_TOPIC_LIKE = 0.15
_BOOST_ENTITY_LIKE = 0.08
_BOOST_ANY_CLOSET = 0.04

# Closet 向量距离上限：距离超过此值的 Closet 不参与 boost
CLOSET_DISTANCE_CAP = 1.5


# ---------------------------------------------------------------------------
# ClosetExtractor - 内容提取器
# ---------------------------------------------------------------------------


class ClosetExtractor:
    """从 Drawer 内容中提取主题、实体、标题、日期行号。"""

    def __init__(self) -> None:
        self._known_systems: list[str] = self._load_known_systems()
        self._coca_words: set[str] = self._load_coca_words()
        self._stoplist: frozenset[str] = _ENTITY_STOPLIST

    def _load_known_systems(self) -> list[str]:
        """加载已知系统名列表。"""
        path = Path(__file__).parent / "data" / "known_systems.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _load_coca_words(self) -> set[str]:
        """加载 COCA 内容词过滤表。"""
        path = Path(__file__).parent / "data" / "coca_content_words.json"
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return set()

    def _apply_known_systems_prepass(self, content: str) -> tuple[list[str], str]:
        """原子检测多词产品名，返回 (entities_found, masked_content)。

        将已知系统名在内容中标记，防止单词提取时被拆分。
        """
        entities: list[str] = []
        masked = content
        for system in self._known_systems:
            if system in masked:
                entities.append(system)
                # Replace with placeholder to prevent word-level extraction
                masked = masked.replace(system, " " * len(system))
        return entities, masked

    # 英文动词 + 上下文（最多 5 个词）
    _EN_TOPIC_RE = re.compile(
        r"\b(" + "|".join(_EN_VERBS) + r")\b"
        r"\s+((?:[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,4}))",
        re.IGNORECASE,
    )

    # 中文动作短语 + 上下文（2-10 个汉字）
    _ZH_TOPIC_RE = re.compile(
        "(" + "|".join(_ZH_VERBS) + r")([\u4e00-\u9fa5]{2,10})"
    )

    # 英文首字母大写词（长度 >= 2）
    _EN_ENTITY_RE = re.compile(r"\b[A-Z][a-z]+\b")

    # 中文括号内专有名词：《》【】「」『』
    _ZH_BRACKET_RE = re.compile(
        r"[《【「『]([^》】」』]{2,4})[》】」』]"
    )

    # 中文双引号内专有名词：" " "
    _ZH_DQUOTE_RE = re.compile(
        '["\u201c\u201d]([^"\u201c\u201d]{2,4})["\u201c\u201d]'
    )

    # 中文单引号内专有名词：' ' '
    _ZH_SQUOTE_RE = re.compile(
        "['\u2018\u2019]([^'\u2018\u2019]{2,4})['\u2018\u2019]"
    )

    # Markdown 标题（# 到 ######）
    _HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    def extract_topics(self, content: str) -> list[str]:
        """提取动作动词 + 上下文短语。

        识别模式：
        - 英文动词过去式/现在式：fixed, added, removed, updated, created, deleted, implemented, refactored
        - 中文动作短语：修复了, 添加了, 删除了, 更新了, 创建了, 实现了
        - 提取动词后紧跟的上下文（最多 5 个词）
        """
        topics: list[str] = []

        # 英文动词 + 上下文
        for m in self._EN_TOPIC_RE.finditer(content):
            verb = m.group(1).lower()
            ctx = m.group(2).strip()
            topics.append(f"{verb} {ctx}")

        # 中文动词 + 上下文
        for m in self._ZH_TOPIC_RE.finditer(content):
            verb = m.group(1)
            ctx = m.group(2)
            topics.append(f"{verb}{ctx}")

        return topics

    def extract_entities(self, content: str) -> list[str]:
        """提取实体名（出现 >= 2 次的首字母大写词）。

        使用 i18n-aware 正则：
        - 英文：首字母大写词 [A-Z][a-z]+（长度 >= 2）
        - 中文：2-4 字专有名词模式（简化：中文括号内内容、引号内专有名词）
        - 过滤常见编程关键字（True, False, None, etc.）
        - 过滤 COCA 内容词和停用词表
        - 已知系统名原子检测（不被拆分）
        - 只保留出现 >= 2 次的实体（已知系统名始终保留）
        """
        # 1. Known systems prepass
        known_entities, masked_content = self._apply_known_systems_prepass(content)

        # 2. Word-level extraction on masked content
        candidates: list[str] = []
        for m in self._EN_ENTITY_RE.finditer(masked_content):
            word = m.group(0)
            if word in _PROGRAMMING_KEYWORDS:
                continue
            if word in self._coca_words:
                continue
            if word in self._stoplist:
                continue
            candidates.append(word)

        # 3. Chinese entity extraction (on original content)
        for m in self._ZH_BRACKET_RE.finditer(content):
            candidates.append(m.group(1))

        for m in self._ZH_DQUOTE_RE.finditer(content):
            candidates.append(m.group(1))

        for m in self._ZH_SQUOTE_RE.finditer(content):
            candidates.append(m.group(1))

        # 4. Add known entities
        candidates.extend(known_entities)

        # 5. Keep entities appearing >= 2 times (known systems always kept)
        counter = Counter(candidates)
        result = [e for e, c in counter.items() if c >= 2]
        # Known systems are always included even if count < 2
        for ke in known_entities:
            if ke not in result:
                result.append(ke)
        return result

    def extract_headers(self, content: str) -> list[str]:
        """提取 Markdown 标题。

        匹配 # 到 ###### 开头的行。
        """
        headers: list[str] = []
        for m in self._HEADER_RE.finditer(content):
            headers.append(m.group(2).strip())
        return headers

    def extract_date_line(self, content: str, filed_at: str = "") -> str:
        """提取日期和行号范围。

        格式：YYYY-MM-DD:Lstart-Lend
        - 日期从 filed_at 提取（取日期部分），若无则用当天日期
        - 行号范围为内容的首行到末行（1-based）
        """
        if filed_at:
            date_part = filed_at[:10]
        else:
            date_part = datetime.date.today().isoformat()

        line_count = len(content.splitlines()) if content else 1
        return f"{date_part}:1-{line_count}"

    def extract_quotes(self, content: str) -> list[str]:
        """提取 15-150 字符的双引号内容作为引用。"""
        quotes: list[str] = []
        # English double quotes
        for m in re.finditer(r'"([^"]{15,150})"', content):
            quotes.append(m.group(1))
        # Chinese double quotes
        for m in re.finditer(r'\u201c([^\u201d]{15,150})\u201d', content):
            quotes.append(m.group(1))
        return quotes

    def build_closet_entries(self, drawer: Drawer) -> list[ClosetEntry]:
        """为单个 Drawer 构建 Closet 条目。

        将 topics、entities、headers 合并为多条 ClosetEntry。
        每条 ClosetSize 约 1500 字符（贪婪打包）。
        使用 drawer.source_file 的哈希作为 source_hash。
        date_line 使用 extract_date_line 结果。
        drawer_ids 包含当前 drawer.id。
        """
        topics = self.extract_topics(drawer.content)
        entities = self.extract_entities(drawer.content)
        headers = self.extract_headers(drawer.content)
        quotes = self.extract_quotes(drawer.content)

        source_hash = content_hash(drawer.source_file)
        date_line = self.extract_date_line(drawer.content, drawer.filed_at)
        drawer_ids = drawer.id
        created_at = datetime.datetime.now().isoformat()

        # 合并 items：topics + headers + quotes 归入 topic 字段，entities 归入 entities 字段
        items: list[tuple[str, str]] = []
        for t in topics + headers + quotes:
            items.append(("topic", t))
        for e in entities:
            items.append(("entity", e))

        # 贪婪打包：累积 items 直到超过目标大小则刷新
        entries: list[ClosetEntry] = []
        current: list[tuple[str, str]] = []
        current_size = 0

        for kind, text in items:
            if current_size + len(text) > _CLOSET_TARGET_SIZE and current:
                entries.append(
                    self._pack_entry(
                        current, source_hash, date_line,
                        drawer_ids, created_at, len(entries),
                    )
                )
                current = []
                current_size = 0
            current.append((kind, text))
            current_size += len(text)

        if current:
            entries.append(
                self._pack_entry(
                    current, source_hash, date_line,
                    drawer_ids, created_at, len(entries),
                )
            )

        # 无内容时创建空条目（仍记录 date_line 和 drawer_ids）
        if not entries:
            entries.append(
                ClosetEntry(
                    id=f"closet_{source_hash[:12]}_0",
                    source_hash=source_hash,
                    topic="",
                    entities="",
                    date_line=date_line,
                    drawer_ids=drawer_ids,
                    created_at=created_at,
                )
            )

        return entries

    @staticmethod
    def _pack_entry(
        items: list[tuple[str, str]],
        source_hash: str,
        date_line: str,
        drawer_ids: str,
        created_at: str,
        index: int,
    ) -> ClosetEntry:
        """将打包后的 items 组装为 ClosetEntry。"""
        topics = [text for kind, text in items if kind == "topic"]
        entities = [text for kind, text in items if kind == "entity"]
        return ClosetEntry(
            id=f"closet_{source_hash[:12]}_{index}",
            source_hash=source_hash,
            topic=";".join(topics),
            entities=";".join(entities),
            date_line=date_line,
            drawer_ids=drawer_ids,
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# ClosetIndexer - 索引管理器
# ---------------------------------------------------------------------------


class ClosetIndexer:
    """Closet 索引管理器 - 构建、存储、查询 Closet 条目。"""

    def __init__(
        self,
        storage: PalaceStorage,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        self.storage = storage
        self.extractor = ClosetExtractor()
        self.embedding_provider = embedding_provider

    def index_drawer(self, drawer: Drawer) -> int:
        """为 Drawer 构建并存储 Closet 条目。

        1. 先删除该 source_hash 的旧 Closet 条目
        2. 提取 topics/entities/headers/date_line
        3. 构建 ClosetEntry 并存入 storage
        4. 返回创建的条目数
        """
        source_hash = content_hash(drawer.source_file)

        # 删除旧条目
        self.storage.delete_closets_by_source(source_hash)

        # 构建并存储新条目
        entries = self.extractor.build_closet_entries(drawer)
        for entry in entries:
            self.storage.add_closet(entry)
            # 当 embedding_provider 可用时，嵌入 topic+entities 文本并存储
            if self.embedding_provider is not None and self.embedding_provider.available:
                closet_text = f"{entry.topic}; {entry.entities}".strip("; ").strip()
                if closet_text:
                    vec = self.embedding_provider.embed(closet_text)
                    if vec is not None:
                        self.storage.update_closet_embedding(
                            entry.id, vector_to_bytes(vec)
                        )

        logger.debug(
            "Indexed drawer %s -> %d closet entries (source=%s)",
            drawer.id,
            len(entries),
            drawer.source_file,
        )
        return len(entries)

    def index_drawers(self, drawers: list[Drawer]) -> int:
        """批量构建 Closet 条目。"""
        total = 0
        for drawer in drawers:
            total += self.index_drawer(drawer)
        return total

    def remove_by_source(self, source_file: str) -> int:
        """删除指定来源文件的所有 Closet 条目。"""
        source_hash = content_hash(source_file)
        return self.storage.delete_closets_by_source(source_hash)

    def get_boost_for_source(
        self, source_file: str, query: str, query_vector: list[float] | None = None
    ) -> float:
        """计算 Closet boost 分数 - 距离减法机制。

        当 query_vector 可用时：
        - 对每个 Closet 计算向量距离
        - 距离 > CLOSET_DISTANCE_CAP 的跳过
        - boost 从原始距离中减去：effective_dist = max(0, min(2, dist - boost))
        - 返回 best_boost（最大的 boost 值）

        当 query_vector 不可用时（降级）：
        - 保留现有的关键词匹配 + 分级 boost 逻辑

        Closet 是信号而非门控：弱 Closet 不会隐藏 Drawer 结果。
        """
        source_hash = content_hash(source_file)
        closets = self.storage.get_closets_by_source(source_hash)

        if not closets:
            return 0.0

        if query_vector is not None:
            # 向量距离减法 boost：找语义距离最近的 Closet
            best_boost = 0.0
            for closet in closets:
                closet_vec_bytes = self.storage.get_closet_embedding(closet.id)
                if closet_vec_bytes is None:
                    continue
                closet_vec = bytes_to_vector(closet_vec_bytes)
                dist = self._cosine_distance(query_vector, closet_vec)
                if dist > CLOSET_DISTANCE_CAP:
                    continue
                # 距离越近 boost 越高：dist=0 -> 0.40, dist=1 -> 0.20
                boost = max(0.0, 0.40 - dist * 0.2)
                best_boost = max(best_boost, boost)
            return best_boost
        else:
            # 降级：关键词匹配 + 分级 boost
            best_boost = 0.0
            query_lower = query.lower()

            for closet in closets:
                topics = [t.strip() for t in closet.topic.split(";") if t.strip()]
                entities = [e.strip() for e in closet.entities.split(";") if e.strip()]

                # Rank 1: topic 精确匹配
                if query in topics:
                    best_boost = max(best_boost, _BOOST_TOPIC_EXACT)
                    continue
                # Rank 2: entities 精确匹配
                if query in entities:
                    best_boost = max(best_boost, _BOOST_ENTITY_EXACT)
                    continue
                # Rank 3: topic LIKE 匹配
                if query_lower in closet.topic.lower():
                    best_boost = max(best_boost, _BOOST_TOPIC_LIKE)
                    continue
                # Rank 4: entities LIKE 匹配
                if query_lower in closet.entities.lower():
                    best_boost = max(best_boost, _BOOST_ENTITY_LIKE)
                    continue

            # Rank 5: Closet 存在但无关键词匹配，给予最小 boost
            if closets and best_boost < _BOOST_ANY_CLOSET:
                best_boost = _BOOST_ANY_CLOSET

            return best_boost

    def search_closets_semantic(
        self, query_vector: list[float], limit: int = 20
    ) -> list[tuple[ClosetEntry, float]]:
        """向量搜索 Closet 条目。

        获取所有 Closet，计算余弦距离，按距离升序返回。

        Args:
            query_vector: 查询向量
            limit: 返回上限

        Returns:
            [(ClosetEntry, distance), ...] 按距离升序排列
        """
        closets = self.storage.list_all_closets(limit=10000)
        if not closets:
            return []

        scored: list[tuple[ClosetEntry, float]] = []
        for closet in closets:
            closet_vec_bytes = self.storage.get_closet_embedding(closet.id)
            if closet_vec_bytes is None:
                continue
            closet_vec = bytes_to_vector(closet_vec_bytes)
            dist = self._cosine_distance(query_vector, closet_vec)
            scored.append((closet, dist))

        scored.sort(key=lambda x: x[1])
        return scored[:limit]

    @staticmethod
    def _cosine_distance(vec_a: list[float], vec_b: list[float]) -> float:
        """计算余弦距离（1 - 余弦相似度）。

        Args:
            vec_a: 向量 A
            vec_b: 向量 B

        Returns:
            余弦距离 [0, 2]，维度不匹配或零向量返回 2.0
        """
        if len(vec_a) != len(vec_b) or not vec_a:
            return 2.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 2.0
        return 1.0 - dot / (norm_a * norm_b)
