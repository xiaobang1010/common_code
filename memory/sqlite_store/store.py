"""记忆宫殿存储后端 - SQLite + FTS5 全文检索。

Palace 隐喻的持久化层：
  - drawers 表：抽屉（原始文本片段）
  - drawers_fts：FTS5 全文索引（trigram 分词器支持中文）
  - closets 表：壁橱（搜索索引指针）
  - kg_triples 表：知识图谱三元组（带时间有效期）

线程安全：写操作使用 threading.Lock 保护，连接按操作创建（check_same_thread=False）。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from memory.palace.models import ClosetEntry, Drawer, KGTriple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 行转换辅助函数
# ---------------------------------------------------------------------------


def _row_to_drawer(row: sqlite3.Row) -> Drawer:
    """将 sqlite3.Row 转换为 Drawer 对象。"""
    return Drawer(
        id=row["id"],
        wing=row["wing"],
        room=row["room"],
        content=row["content"],
        source_file=row["source_file"],
        filed_at=row["filed_at"],
        authored_at=row["authored_at"],
        chunk_index=row["chunk_index"],
        importance=row["importance"],
        source_mtime=row["source_mtime"],
        content_hash=row["content_hash"],
    )


def _row_to_closet(row: sqlite3.Row) -> ClosetEntry:
    """将 sqlite3.Row 转换为 ClosetEntry 对象。"""
    return ClosetEntry(
        id=row["id"],
        source_hash=row["source_hash"],
        topic=row["topic"],
        entities=row["entities"],
        date_line=row["date_line"],
        drawer_ids=row["drawer_ids"],
        created_at=row["created_at"],
    )


def _row_to_triple(row: sqlite3.Row) -> KGTriple:
    """将 sqlite3.Row 转换为 KGTriple 对象。"""
    return KGTriple(
        id=row["id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        drawer_refs=row["drawer_refs"],
        created_at=row["created_at"],
        confidence=row["confidence"],
        source_file=row["source_file"],
        source_drawer_id=row["source_drawer_id"],
        extracted_at=row["extracted_at"],
    )


# ---------------------------------------------------------------------------
# PalaceStorage - 存储后端
# ---------------------------------------------------------------------------


class PalaceStorage:
    """记忆宫殿 SQLite 存储后端。

    管理 drawers（抽屉）、closets（壁橱）、kg_triples（知识图谱）三张表，
    以及 drawers_fts（FTS5 全文索引）。通过触发器自动同步 FTS 索引。

    默认数据库路径：~/.agent/memory/palace.sqlite3

    Attributes:
        db_path: 数据库文件路径
        _lock: 写操作线程锁
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".agent" / "memory" / "palace.sqlite3"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        """创建并返回一个新的数据库连接。

        使用 check_same_thread=False 允许跨线程访问。
        row_factory 设为 sqlite3.Row 以支持按列名访问。
        调用方负责关闭连接。
        """
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # -----------------------------------------------------------------------
    # Schema 初始化
    # -----------------------------------------------------------------------

    def init_schema(self) -> None:
        """创建所有表、索引和触发器。

        幂等操作，可安全多次调用。FTS5 分词器优先使用 trigram（支持中文），
        不可用时回退到 unicode61。
        """
        conn = self._get_conn()
        try:
            # --- 启用 WAL 模式，提升并发读写性能 ---
            conn.execute("PRAGMA journal_mode=WAL;")

            # --- drawers 表 ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drawers (
                    id TEXT PRIMARY KEY,
                    wing TEXT NOT NULL,
                    room TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_file TEXT DEFAULT '',
                    filed_at TEXT NOT NULL,
                    authored_at TEXT DEFAULT '',
                    chunk_index INTEGER DEFAULT 0,
                    importance REAL DEFAULT 0.5,
                    source_mtime REAL,
                    content_hash TEXT NOT NULL
                )
                """
            )

            # --- closets 表 ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS closets (
                    id TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    topic TEXT DEFAULT '',
                    entities TEXT DEFAULT '',
                    date_line TEXT DEFAULT '',
                    drawer_ids TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )

            # --- kg_triples 表 ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kg_triples (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    drawer_refs TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )

            # --- kg_triples 表迁移：新增列（已存在则跳过） ---
            for alter_sql in (
                "ALTER TABLE kg_triples ADD COLUMN confidence REAL DEFAULT 1.0",
                "ALTER TABLE kg_triples ADD COLUMN source_file TEXT DEFAULT ''",
                "ALTER TABLE kg_triples ADD COLUMN source_drawer_id TEXT DEFAULT ''",
                "ALTER TABLE kg_triples ADD COLUMN extracted_at TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(alter_sql)
                except sqlite3.OperationalError:
                    pass

            # --- drawers 表迁移：新增 embedding 列（已存在则跳过） ---
            try:
                conn.execute("ALTER TABLE drawers ADD COLUMN embedding BLOB")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # --- closets 表迁移：新增 embedding 列（已存在则跳过） ---
            try:
                conn.execute("ALTER TABLE closets ADD COLUMN embedding BLOB")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # --- entities 表 ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT DEFAULT '',
                    properties TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"
            )

            # --- embeddings_cache 表：向量缓存 ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings_cache (
                    text_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (text_hash, model)
                )
                """
            )

            # --- embedder_identity 表：嵌入模型身份记录 ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedder_identity (
                    model_name TEXT PRIMARY KEY,
                    dimension INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )

            # --- 嵌入模型身份软检查：记录已存在的身份（不阻断） ---
            try:
                identity_rows = conn.execute(
                    "SELECT model_name, dimension, recorded_at FROM embedder_identity"
                ).fetchall()
                if identity_rows:
                    for row in identity_rows:
                        logger.info(
                            "已记录的嵌入模型身份: model=%s, dim=%d, recorded_at=%s",
                            row["model_name"], row["dimension"], row["recorded_at"],
                        )
            except sqlite3.OperationalError:
                pass  # 表尚未就绪，忽略

            # --- hallways 表：后挖掘图分析（走廊/隧道） ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hallways (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    wing_from TEXT NOT NULL,
                    wing_to TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    drawer_ids TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hallways_type ON hallways(type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hallways_wing ON hallways(wing_from)"
            )

            # --- FTS5 全文索引（trigram 优先，回退 unicode61） ---
            fts_sql_trigram = """
                CREATE VIRTUAL TABLE IF NOT EXISTS drawers_fts USING fts5(
                    content,
                    wing,
                    room,
                    source_file,
                    content='drawers',
                    content_rowid='rowid',
                    tokenize='trigram'
                )
            """
            fts_sql_unicode61 = """
                CREATE VIRTUAL TABLE IF NOT EXISTS drawers_fts USING fts5(
                    content,
                    wing,
                    room,
                    source_file,
                    content='drawers',
                    content_rowid='rowid',
                    tokenize='unicode61'
                )
            """
            try:
                conn.execute(fts_sql_trigram)
            except sqlite3.OperationalError:
                logger.warning("trigram 分词器不可用，回退到 unicode61")
                conn.execute(fts_sql_unicode61)

            # --- FTS5 同步触发器 ---
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS drawers_ai AFTER INSERT ON drawers BEGIN
                    INSERT INTO drawers_fts(rowid, content, wing, room, source_file)
                    VALUES (new.rowid, new.content, new.wing, new.room, new.source_file);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS drawers_ad AFTER DELETE ON drawers BEGIN
                    INSERT INTO drawers_fts(drawers_fts, rowid, content, wing, room, source_file)
                    VALUES ('delete', old.rowid, old.content, old.wing, old.room, old.source_file);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS drawers_au AFTER UPDATE ON drawers BEGIN
                    INSERT INTO drawers_fts(drawers_fts, rowid, content, wing, room, source_file)
                    VALUES ('delete', old.rowid, old.content, old.wing, old.room, old.source_file);
                    INSERT INTO drawers_fts(rowid, content, wing, room, source_file)
                    VALUES (new.rowid, new.content, new.wing, new.room, new.source_file);
                END
                """
            )

            # --- 索引 ---
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drawers_wing ON drawers(wing)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drawers_wing_room ON drawers(wing, room)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drawers_source ON drawers(source_file)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drawers_hash ON drawers(content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_closets_source ON closets(source_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_subject ON kg_triples(subject)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_object ON kg_triples(object)")

            conn.commit()
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Drawer CRUD
    # -----------------------------------------------------------------------

    def add_drawer(self, drawer: Drawer) -> bool:
        """插入抽屉并同步 FTS 索引。

        通过触发器自动同步 drawers_fts。重复 ID（主键冲突）时跳过插入。

        Args:
            drawer: 抽屉对象

        Returns:
            True 插入成功，False 表示 ID 已存在（重复）
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO drawers
                        (id, wing, room, content, source_file, filed_at, authored_at,
                         chunk_index, importance, source_mtime, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        drawer.id,
                        drawer.wing,
                        drawer.room,
                        drawer.content,
                        drawer.source_file,
                        drawer.filed_at,
                        drawer.authored_at,
                        drawer.chunk_index,
                        drawer.importance,
                        drawer.source_mtime,
                        drawer.content_hash,
                    ),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_drawer(self, drawer_id: str) -> Drawer | None:
        """按 ID 获取单个抽屉。

        Args:
            drawer_id: 抽屉 ID

        Returns:
            抽屉对象，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM drawers WHERE id = ?", (drawer_id,)
            ).fetchone()
            return _row_to_drawer(row) if row else None
        finally:
            conn.close()

    def delete_drawer(self, drawer_id: str) -> bool:
        """删除抽屉并清理 FTS 索引。

        通过触发器自动清理 drawers_fts。

        Args:
            drawer_id: 抽屉 ID

        Returns:
            True 删除成功，False 表示抽屉不存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "DELETE FROM drawers WHERE id = ?", (drawer_id,)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def delete_by_source(self, source_file: str) -> int:
        """删除指定来源文件的所有抽屉。

        通过触发器自动清理 FTS 索引。

        Args:
            source_file: 来源文件路径

        Returns:
            删除的抽屉数量
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "DELETE FROM drawers WHERE source_file = ?", (source_file,)
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def list_drawers(
        self,
        wing: str | None = None,
        room: str | None = None,
        source_file: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Drawer]:
        """列出抽屉，支持按 wing/room/source_file 过滤。

        Args:
            wing: 顶层命名空间过滤，None 不过滤
            room: 子分类过滤，None 不过滤
            source_file: 来源文件过滤，None 不过滤
            limit: 返回上限
            offset: 偏移量

        Returns:
            抽屉列表，按 filed_at DESC 排序
        """
        conn = self._get_conn()
        try:
            query = "SELECT * FROM drawers WHERE 1=1"
            params: list = []
            if wing is not None:
                query += " AND wing = ?"
                params.append(wing)
            if room is not None:
                query += " AND room = ?"
                params.append(room)
            if source_file is not None:
                query += " AND source_file = ?"
                params.append(source_file)
            query += " ORDER BY filed_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [_row_to_drawer(row) for row in rows]
        finally:
            conn.close()

    def list_drawers_by_importance(
        self, limit: int = 15, wing: str | None = None
    ) -> list[Drawer]:
        """按重要性排序列出抽屉。

        排序：importance DESC, filed_at DESC。

        Args:
            limit: 返回上限
            wing: 顶层命名空间过滤，None 不过滤

        Returns:
            抽屉列表
        """
        conn = self._get_conn()
        try:
            if wing is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM drawers WHERE wing = ?
                    ORDER BY importance DESC, filed_at DESC LIMIT ?
                    """,
                    (wing, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM drawers
                    ORDER BY importance DESC, filed_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [_row_to_drawer(row) for row in rows]
        finally:
            conn.close()

    def search_fts(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        source_file: str | None = None,
        limit: int = 10,
    ) -> list[tuple[Drawer, float]]:
        """FTS5 全文检索，返回候选集（score 为占位 0.0）。

        FTS5 仅用于 MATCH 候选召回，评分由上层 HybridSearcher 自实现 BM25 完成。
        支持 wing/room/source_file 过滤。

        Args:
            query: 搜索关键词
            wing: 顶层命名空间过滤，None 不过滤
            room: 子分类过滤，None 不过滤
            source_file: 来源文件过滤，None 不过滤
            limit: 返回上限

        Returns:
            [(抽屉对象, 0.0 占位分数), ...] 按 filed_at 降序排列
        """
        conn = self._get_conn()
        try:
            # 转义 FTS5 查询：双引号转义后包裹在双引号中作为短语查询
            escaped = query.replace('"', '""')
            fts_query = f'"{escaped}"'

            sql = (
                "SELECT drawers.*, 0.0 as score "
                "FROM drawers_fts "
                "JOIN drawers ON drawers.rowid = drawers_fts.rowid "
                "WHERE drawers_fts MATCH ?"
            )
            params: list = [fts_query]

            if wing is not None:
                sql += " AND drawers.wing = ?"
                params.append(wing)
            if room is not None:
                sql += " AND drawers.room = ?"
                params.append(room)
            if source_file is not None:
                sql += " AND drawers.source_file = ?"
                params.append(source_file)

            sql += " ORDER BY drawers.filed_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            return [(_row_to_drawer(row), row["score"]) for row in rows]
        finally:
            conn.close()

    def count_drawers(self, wing: str | None = None) -> int:
        """统计抽屉数量。

        Args:
            wing: 顶层命名空间过滤，None 统计全部

        Returns:
            抽屉总数
        """
        conn = self._get_conn()
        try:
            if wing is not None:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM drawers WHERE wing = ?", (wing,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM drawers"
                ).fetchone()
            return row["cnt"]
        finally:
            conn.close()

    def list_wings(self) -> list[tuple[str, int]]:
        """列出所有 wing 及其抽屉数量。

        Returns:
            [(wing名称, 抽屉数量), ...] 按 wing 名称排序
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT wing, COUNT(*) as cnt FROM drawers GROUP BY wing ORDER BY wing"
            ).fetchall()
            return [(row["wing"], row["cnt"]) for row in rows]
        finally:
            conn.close()

    def list_rooms(self, wing: str) -> list[tuple[str, int]]:
        """列出指定 wing 下的所有 room 及其抽屉数量。

        Args:
            wing: 顶层命名空间

        Returns:
            [(room名称, 抽屉数量), ...] 按 room 名称排序
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT room, COUNT(*) as cnt FROM drawers
                WHERE wing = ? GROUP BY room ORDER BY room
                """,
                (wing,),
            ).fetchall()
            return [(row["room"], row["cnt"]) for row in rows]
        finally:
            conn.close()

    def get_source_mtime(self, source_file: str) -> float | None:
        """获取来源文件的最新 source_mtime。

        用于增量更新判断：如果文件 mtime 未变，跳过重新索引。

        Args:
            source_file: 来源文件路径

        Returns:
            最新 source_mtime，无记录返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(source_mtime) as max_mtime FROM drawers WHERE source_file = ?",
                (source_file,),
            ).fetchone()
            return row["max_mtime"]
        finally:
            conn.close()

    def check_content_exists(self, content_hash: str) -> bool:
        """检查指定 content_hash 是否已存在。

        用于内容去重。

        Args:
            content_hash: 内容 SHA-256 哈希

        Returns:
            True 已存在，False 不存在
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM drawers WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Closet CRUD
    # -----------------------------------------------------------------------

    def add_closet(self, entry: ClosetEntry) -> bool:
        """插入壁橱条目。

        Args:
            entry: 壁橱条目

        Returns:
            True 插入成功，False 表示 ID 已存在（重复）
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO closets
                        (id, source_hash, topic, entities, date_line, drawer_ids, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.source_hash,
                        entry.topic,
                        entry.entities,
                        entry.date_line,
                        entry.drawer_ids,
                        entry.created_at,
                    ),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_closets_by_source(self, source_hash: str) -> list[ClosetEntry]:
        """按来源哈希获取壁橱条目。

        Args:
            source_hash: 来源文件路径的哈希

        Returns:
            壁橱条目列表，按 created_at 排序
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM closets WHERE source_hash = ? ORDER BY created_at",
                (source_hash,),
            ).fetchall()
            return [_row_to_closet(row) for row in rows]
        finally:
            conn.close()

    def delete_closets_by_source(self, source_hash: str) -> int:
        """删除指定来源的所有壁橱条目。

        Args:
            source_hash: 来源文件路径的哈希

        Returns:
            删除的条目数量
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "DELETE FROM closets WHERE source_hash = ?", (source_hash,)
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def search_closets(self, query: str, limit: int = 20) -> list[ClosetEntry]:
        """在壁橱条目的 topic 和 entities 字段中搜索。

        使用 LIKE 模糊匹配。

        Args:
            query: 搜索关键词
            limit: 返回上限

        Returns:
            匹配的壁橱条目列表，按 created_at DESC 排序
        """
        conn = self._get_conn()
        try:
            pattern = f"%{query}%"
            rows = conn.execute(
                """
                SELECT * FROM closets
                WHERE topic LIKE ? OR entities LIKE ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (pattern, pattern, limit),
            ).fetchall()
            return [_row_to_closet(row) for row in rows]
        finally:
            conn.close()

    def list_all_closets(self, limit: int = 1000) -> list[ClosetEntry]:
        """列出所有壁橱条目。

        Args:
            limit: 返回上限

        Returns:
            壁橱条目列表，按 created_at DESC 排序
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM closets ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_row_to_closet(row) for row in rows]
        finally:
            conn.close()

    def update_closet_embedding(self, closet_id: str, vector_bytes: bytes) -> None:
        """更新壁橱条目的嵌入向量。

        Args:
            closet_id: 壁橱条目 ID
            vector_bytes: 序列化后的向量 bytes
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE closets SET embedding = ? WHERE id = ?",
                    (vector_bytes, closet_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_closet_embedding(self, closet_id: str) -> bytes | None:
        """获取壁橱条目的嵌入向量。

        Args:
            closet_id: 壁橱条目 ID

        Returns:
            序列化的向量 bytes，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT embedding FROM closets WHERE id = ?", (closet_id,)
            ).fetchone()
            if row is None or row["embedding"] is None:
                return None
            return row["embedding"]
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # KG Triple CRUD
    # -----------------------------------------------------------------------

    def add_triple(self, triple: KGTriple) -> bool:
        """插入知识图谱三元组。

        Args:
            triple: 三元组

        Returns:
            True 插入成功，False 表示 ID 已存在（重复）
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO kg_triples
                        (id, subject, predicate, object, valid_from, valid_to,
                         drawer_refs, created_at, confidence, source_file,
                         source_drawer_id, extracted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        triple.id,
                        triple.subject,
                        triple.predicate,
                        triple.object,
                        triple.valid_from,
                        triple.valid_to,
                        triple.drawer_refs,
                        triple.created_at,
                        triple.confidence,
                        triple.source_file,
                        triple.source_drawer_id,
                        triple.extracted_at,
                    ),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def query_triples_by_entity(
        self, entity: str, as_of: str | None = None
    ) -> list[KGTriple]:
        """查询主体的三元组，支持时间点过滤。

        对日期进行归一化处理：长度为 10 的日期（YYYY-MM-DD）补全为
        带时间的 ISO 8601 格式后再比较，确保日期和完整时间戳可正确比较。

        Args:
            entity: 主体实体名
            as_of: 时间点过滤（ISO 8601），只返回 valid_from <= as_of
                    且 valid_to 为 None 或 > as_of 的三元组。None 不过滤。

        Returns:
            三元组列表，按 valid_from 排序
        """
        conn = self._get_conn()
        try:
            if as_of is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_triples
                    WHERE subject = ?
                      AND CASE
                        WHEN length(valid_from) = 10 THEN valid_from || 'T00:00:00Z'
                        ELSE valid_from
                      END <= ?
                      AND (
                        valid_to IS NULL OR
                        CASE
                          WHEN length(valid_to) = 10 THEN valid_to || 'T23:59:59Z'
                          ELSE valid_to
                        END > ?
                      )
                    ORDER BY valid_from
                    """,
                    (entity, as_of, as_of),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_triples WHERE subject = ?
                    ORDER BY valid_from
                    """,
                    (entity,),
                ).fetchall()
            return [_row_to_triple(row) for row in rows]
        finally:
            conn.close()

    def query_triples_by_object(
        self, entity: str, as_of: str | None = None
    ) -> list[KGTriple]:
        """查询客体的三元组，支持时间点过滤。

        对日期进行归一化处理：长度为 10 的日期（YYYY-MM-DD）补全为
        带时间的 ISO 8601 格式后再比较。

        Args:
            entity: 客体实体名
            as_of: 时间点过滤（ISO 8601），只返回 valid_from <= as_of
                    且 valid_to 为 None 或 > as_of 的三元组。None 不过滤。

        Returns:
            三元组列表，按 valid_from 排序
        """
        conn = self._get_conn()
        try:
            if as_of is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_triples
                    WHERE object = ?
                      AND CASE
                        WHEN length(valid_from) = 10 THEN valid_from || 'T00:00:00Z'
                        ELSE valid_from
                      END <= ?
                      AND (
                        valid_to IS NULL OR
                        CASE
                          WHEN length(valid_to) = 10 THEN valid_to || 'T23:59:59Z'
                          ELSE valid_to
                        END > ?
                      )
                    ORDER BY valid_from
                    """,
                    (entity, as_of, as_of),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_triples WHERE object = ?
                    ORDER BY valid_from
                    """,
                    (entity,),
                ).fetchall()
            return [_row_to_triple(row) for row in rows]
        finally:
            conn.close()

    def query_triples_by_predicate(
        self, predicate: str, as_of: str | None = None
    ) -> list[KGTriple]:
        """按关系类型查询三元组，支持时间点过滤。

        对日期进行归一化处理：长度为 10 的日期（YYYY-MM-DD）补全为
        带时间的 ISO 8601 格式后再比较。

        Args:
            predicate: 关系类型
            as_of: 时间点过滤（ISO 8601），只返回 valid_from <= as_of
                    且 valid_to 为 None 或 > as_of 的三元组。None 不过滤。

        Returns:
            三元组列表，按 valid_from 排序
        """
        conn = self._get_conn()
        try:
            if as_of is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_triples
                    WHERE predicate = ?
                      AND CASE
                        WHEN length(valid_from) = 10 THEN valid_from || 'T00:00:00Z'
                        ELSE valid_from
                      END <= ?
                      AND (
                        valid_to IS NULL OR
                        CASE
                          WHEN length(valid_to) = 10 THEN valid_to || 'T23:59:59Z'
                          ELSE valid_to
                        END > ?
                      )
                    ORDER BY valid_from
                    """,
                    (predicate, as_of, as_of),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM kg_triples WHERE predicate = ?
                    ORDER BY valid_from
                    """,
                    (predicate,),
                ).fetchall()
            return [_row_to_triple(row) for row in rows]
        finally:
            conn.close()

    def query_timeline(self, entity: str) -> list[KGTriple]:
        """查询实体相关的所有三元组时间线。

        查询 subject 或 object 等于 entity 的所有三元组。

        Args:
            entity: 实体名

        Returns:
            三元组列表，按 valid_from 排序
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM kg_triples
                WHERE subject = ? OR object = ?
                ORDER BY valid_from
                """,
                (entity, entity),
            ).fetchall()
            return [_row_to_triple(row) for row in rows]
        finally:
            conn.close()

    def invalidate_triple(
        self, subject: str, predicate: str, object: str, ended: str
    ) -> int:
        """使匹配的三元组失效（设置 valid_to）。

        只更新 valid_to 为 NULL 的三元组。

        Args:
            subject: 主体实体
            predicate: 关系类型
            object: 客体实体
            ended: 失效时间（ISO 8601）

        Returns:
            失效的三元组数量
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE kg_triples SET valid_to = ?
                    WHERE subject = ? AND predicate = ? AND object = ?
                      AND valid_to IS NULL
                    """,
                    (ended, subject, predicate, object),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def list_all_entities(self) -> list[str]:
        """列出知识图谱中的所有实体（主体和客体去重）。

        Returns:
            实体名列表
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT subject AS entity FROM kg_triples
                UNION
                SELECT DISTINCT object AS entity FROM kg_triples
                """
            ).fetchall()
            return [row["entity"] for row in rows]
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Entity CRUD
    # -----------------------------------------------------------------------

    def add_entity(self, entity_id: str, name: str, entity_type: str = "",
                   properties: str = "{}") -> bool:
        """插入实体记录。

        Args:
            entity_id: 实体唯一 ID
            name: 实体名称
            entity_type: 实体类型
            properties: 实体属性（JSON 字符串）

        Returns:
            True 插入成功，False 表示 ID 已存在（重复）
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO entities
                        (id, name, type, properties, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        name,
                        entity_type,
                        properties,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_entity(self, name: str) -> dict | None:
        """按名称获取实体。

        Args:
            name: 实体名称

        Returns:
            实体字典 {id, name, type, properties}，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM entities WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "properties": row["properties"],
            }
        finally:
            conn.close()

    def list_entities_by_type(self, entity_type: str) -> list[dict]:
        """按类型列出实体。

        Args:
            entity_type: 实体类型

        Returns:
            实体字典列表 [{id, name, type, properties}, ...]
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = ?", (entity_type,)
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "type": row["type"],
                    "properties": row["properties"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体。

        Args:
            entity_id: 实体 ID

        Returns:
            True 删除成功，False 表示实体不存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "DELETE FROM entities WHERE id = ?", (entity_id,)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # -----------------------------------------------------------------------
    # Embedding CRUD
    # -----------------------------------------------------------------------

    def update_embedding(self, drawer_id: str, vector_bytes: bytes) -> None:
        """更新抽屉的嵌入向量。

        Args:
            drawer_id: 抽屉 ID
            vector_bytes: 序列化后的向量 bytes
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE drawers SET embedding = ? WHERE id = ?",
                    (vector_bytes, drawer_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_embedding(self, drawer_id: str) -> bytes | None:
        """获取抽屉的嵌入向量。

        Args:
            drawer_id: 抽屉 ID

        Returns:
            序列化的向量 bytes，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT embedding FROM drawers WHERE id = ?", (drawer_id,)
            ).fetchone()
            if row is None or row["embedding"] is None:
                return None
            return row["embedding"]
        finally:
            conn.close()

    def get_cached_embedding(self, text_hash: str, model: str) -> bytes | None:
        """获取缓存的嵌入向量。

        Args:
            text_hash: 文本哈希
            model: 模型名

        Returns:
            序列化的向量 bytes，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT vector FROM embeddings_cache WHERE text_hash = ? AND model = ?",
                (text_hash, model),
            ).fetchone()
            if row is None:
                return None
            return row["vector"]
        finally:
            conn.close()

    def store_embedding(self, text_hash: str, model: str, vector_bytes: bytes) -> None:
        """存储嵌入向量到缓存。

        Args:
            text_hash: 文本哈希
            model: 模型名
            vector_bytes: 序列化后的向量 bytes
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO embeddings_cache
                        (text_hash, model, vector, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        text_hash,
                        model,
                        vector_bytes,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def check_embedder_identity(self, model_name: str) -> tuple[int, str] | None:
        """检查嵌入模型身份记录。

        Args:
            model_name: 模型名

        Returns:
            (dimension, recorded_at) 元组，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT dimension, recorded_at FROM embedder_identity WHERE model_name = ?",
                (model_name,),
            ).fetchone()
            if row is None:
                return None
            return (row["dimension"], row["recorded_at"])
        finally:
            conn.close()

    def record_embedder_identity(self, model_name: str, dimension: int) -> None:
        """记录嵌入模型身份。

        Args:
            model_name: 模型名
            dimension: 向量维度
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO embedder_identity
                        (model_name, dimension, recorded_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        model_name,
                        dimension,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
