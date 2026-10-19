"""会话持久化存储层 - SQLite。

把对话历史从内存搬到磁盘，服务器重启后还能恢复。

两张表：
  - sessions: 每条记录是一次对话会话（含完整消息列表，JSON 序列化）
  - workspaces: 工作区（项目目录）登记表，一个工作区可以有多个会话

线程安全：写操作用 threading.Lock 保护，连接按操作创建（check_same_thread=False）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from session.models import Session, TaskGroup, Workspace

logger = logging.getLogger(__name__)


class SessionStore:
    """会话和工作区的 SQLite 存储层。

    默认数据库路径：~/.agent/sessions.db
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".agent" / "sessions.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """创建并返回一个新的数据库连接。

        使用 check_same_thread=False 允许跨线程访问。
        row_factory 设为 sqlite3.Row 以支持按列名访问。
        调用方负责关闭连接。
        """
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """建表，幂等操作，可安全多次调用。"""
        conn = self._get_conn()
        try:
            conn.execute("PRAGMA journal_mode=WAL;")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workspace_path TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    branch TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    messages TEXT DEFAULT '[]',
                    pinned INTEGER DEFAULT 0,
                    spec_name TEXT DEFAULT '',
                    parent_session_id TEXT,
                    origin TEXT DEFAULT 'chat',
                    agent_meta TEXT DEFAULT '{}',
                    last_turn TEXT DEFAULT '{}'
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    path TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    last_used_at TEXT NOT NULL,
                    pinned INTEGER DEFAULT 0,
                    alias TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    color TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )

            # 统一输入队列：运行中用户消息与后台通知同表排队，轮次边界转正。
            # status 终态 promoted/cancelled/discarded，入队初始态 queued；
            # 撤销与守卫类终态经 status_reason 区分（user_removed 等）
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_input (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    delivery TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    admitted_sequence INTEGER NOT NULL,
                    promoted_sequence INTEGER,
                    promoted_message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    status_reason TEXT,
                    time_created REAL NOT NULL,
                    time_updated REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_input_queue "
                "ON session_input(session_id, status, admitted_sequence)"
            )

            # 会话级队列状态：auto_drain 关时两个转正点位全跳过；
            # pause_reason 取值 manual/error/stopped
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_queue_state (
                    session_id TEXT PRIMARY KEY,
                    auto_drain INTEGER NOT NULL DEFAULT 1,
                    pause_reason TEXT,
                    time_updated REAL NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT '',
                    sequence INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    time_created INTEGER NOT NULL,
                    time_updated INTEGER NOT NULL,
                    UNIQUE(session_id, sequence)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS part (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL REFERENCES message(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    time_created INTEGER NOT NULL,
                    time_updated INTEGER NOT NULL,
                    UNIQUE(message_id, sequence)
                )
                """
            )
            # 图片字节外部化存储：行内只存 {attachment_id,mime} 引用；
            # 身份取内容哈希（同图文件只落一份），但按会话各登记一行——
            # 整批重建的 GC 只清理本会话不再引用且无他会话登记的附件，
            # 不会把仍被别的会话消息引用的文件误删
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attachment (
                    id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    mime TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    bytes INTEGER NOT NULL DEFAULT 0,
                    time_created INTEGER NOT NULL,
                    PRIMARY KEY (id, session_id)
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_session ON message(session_id, sequence)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_part_session ON part(session_id)"
            )

            # 兼容旧库：缺列时补列（ALTER TABLE 幂等）
            self._ensure_columns(conn)

            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        """旧库迁移：按需补充新列（pinned/alias），不破坏既有数据。"""
        session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "pinned" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0")

        ws_cols = {r[1] for r in conn.execute("PRAGMA table_info(workspaces)").fetchall()}
        if "pinned" not in ws_cols:
            conn.execute("ALTER TABLE workspaces ADD COLUMN pinned INTEGER DEFAULT 0")
        if "alias" not in ws_cols:
            conn.execute("ALTER TABLE workspaces ADD COLUMN alias TEXT DEFAULT ''")
        if "created_at" not in ws_cols:
            conn.execute("ALTER TABLE workspaces ADD COLUMN created_at TEXT DEFAULT ''")
            # 存量行回填同一基值时间戳：列表顺序由查询的 rowid DESC tie-break 保证
            # （同值时后插入在前=旧插入序），新插入行 created_at 必晚于基值、排在存量前；
            # created_at = '' 过滤保证重复启动幂等，不重复回填
            conn.execute(
                "UPDATE workspaces SET created_at = ? WHERE created_at = ''",
                (datetime.now().isoformat(),),
            )

        session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        # 行式转录：messages_backup 保存迁移前的整表快照（回退资料），
        # migrated_at>0 表示该会话消息真相源已切到 message/part 行表
        if "messages_backup" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN messages_backup TEXT DEFAULT ''")
        if "migrated_at" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN migrated_at INTEGER DEFAULT 0")
        if "group_id" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN group_id TEXT DEFAULT ''")
        if "spec_name" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN spec_name TEXT DEFAULT ''")
        # 子代理执行底座：子会话三列（父会话指针 / 来源 / 代理元数据）
        if "parent_session_id" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN parent_session_id TEXT")
        if "origin" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN origin TEXT DEFAULT 'chat'")
        if "agent_meta" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN agent_meta TEXT DEFAULT '{}'")
        # 回合退出原因持久化：最近一回合的退出信息（reason/error/finished_at/user_ts）
        if "last_turn" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_turn TEXT DEFAULT '{}'")

    # ------------------------------------------------------------------
    # 行转换辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_session(row: sqlite3.Row, include_messages: bool = True) -> Session:
        """把数据库行转成 Session 对象。

        include_messages 为 False 时不反序列化 messages（列表场景下省流量），
        只填充 message_count。
        """
        messages: list[dict] = []
        message_count = 0
        if include_messages and row["messages"]:
            try:
                messages = json.loads(row["messages"])
                message_count = len(messages)
            except (json.JSONDecodeError, TypeError):
                messages = []
        elif row["messages"]:
            # 不反序列化但需要计数
            try:
                message_count = len(json.loads(row["messages"]))
            except (json.JSONDecodeError, TypeError):
                message_count = 0

        # agent_meta 列存 JSON 文本，坏值容错为空 dict
        agent_meta: dict = {}
        if "agent_meta" in row.keys() and row["agent_meta"]:
            try:
                parsed = json.loads(row["agent_meta"])
                if isinstance(parsed, dict):
                    agent_meta = parsed
            except (json.JSONDecodeError, TypeError):
                agent_meta = {}

        # last_turn 列存最近回合退出信息 JSON，坏值容错为空 dict
        last_turn: dict = {}
        if "last_turn" in row.keys() and row["last_turn"]:
            try:
                parsed_turn = json.loads(row["last_turn"])
                if isinstance(parsed_turn, dict):
                    last_turn = parsed_turn
            except (json.JSONDecodeError, TypeError):
                last_turn = {}

        return Session(
            id=row["id"],
            workspace_path=row["workspace_path"],
            title=row["title"],
            branch=row["branch"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            messages=messages,
            message_count=message_count,
            pinned=bool(row["pinned"]) if "pinned" in row.keys() else False,
            # 旧库迁移前可能缺列，按列存在性兼容读取，避免读出恒为空串
            group_id=row["group_id"] if "group_id" in row.keys() else "",
            parent_session_id=(
                row["parent_session_id"]
                if "parent_session_id" in row.keys()
                else None
            ),
            origin=(
                row["origin"]
                if "origin" in row.keys() and row["origin"]
                else "chat"
            ),
            agent_meta=agent_meta,
            last_turn=last_turn,
        )

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row, session_count: int = 0) -> Workspace:
        """把数据库行转成 Workspace 对象。"""
        return Workspace(
            path=row["path"],
            name=row["name"],
            last_used_at=row["last_used_at"],
            session_count=session_count,
            pinned=bool(row["pinned"]) if "pinned" in row.keys() else False,
            alias=row["alias"] if "alias" in row.keys() else "",
            created_at=row["created_at"] if "created_at" in row.keys() else "",
        )

    @staticmethod
    def _parse_blob(blob: object) -> list[dict]:
        try:
            msgs = json.loads(blob or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [m for m in msgs if isinstance(m, dict)]

    @staticmethod
    def _message_counts(conn: sqlite3.Connection) -> dict[str, int]:
        """各会话行数（行式会话的 message_count 真相源，列表路径批量取）。"""
        return {
            r[0]: r[1]
            for r in conn.execute("SELECT session_id, COUNT(*) FROM message GROUP BY session_id").fetchall()
        }

    @staticmethod
    def _row_to_task_group(row: sqlite3.Row) -> TaskGroup:
        """把数据库行转成 TaskGroup 对象。"""
        return TaskGroup(
            id=row["id"],
            name=row["name"],
            color=row["color"] if "color" in row.keys() else "",
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # 会话 CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        workspace_path: str,
        title: str = "",
        branch: str = "",
        session_id: str | None = None,
        origin: str = "chat",
        parent_session_id: str | None = None,
    ) -> Session:
        """创建新会话，同时确保工作区已登记。

        Args:
            workspace_path: 工作区路径
            title: 会话标题，可留空（后续自动生成）
            branch: 创建时的 git 分支
            session_id: 显式会话 id（子会话按确定值创建；缺省生成 UUID）
            origin: 会话来源（"chat" / "subagent"）
            parent_session_id: 父会话 id（子会话指向主对话会话）

        Returns:
            新建的 Session 对象
        """
        now = datetime.now().isoformat()
        if not session_id:
            session_id = str(uuid.uuid4())

        with self._lock:
            conn = self._get_conn()
            try:
                # 确保 workspace 在表中
                conn.execute(
                    """
                    INSERT OR IGNORE INTO workspaces (path, name, last_used_at)
                    VALUES (?, ?, ?)
                    """,
                    (workspace_path, os.path.basename(workspace_path), now),
                )

                conn.execute(
                    """
                    INSERT INTO sessions
                        (id, workspace_path, title, branch, created_at, updated_at,
                         messages, origin, parent_session_id, agent_meta)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                    """,
                    (
                        session_id,
                        workspace_path,
                        title,
                        branch,
                        now,
                        now,
                        "[]",
                        origin,
                        parent_session_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        return Session(
            id=session_id,
            workspace_path=workspace_path,
            title=title,
            branch=branch,
            created_at=now,
            updated_at=now,
            messages=[],
            message_count=0,
            origin=origin,
            parent_session_id=parent_session_id,
        )

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在（子会话 upsert 判定用）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def update_session_agent_meta(self, session_id: str, meta: dict) -> bool:
        """更新子会话的代理元数据（JSON 整段覆盖），同时刷新 updated_at。

        Args:
            session_id: 会话 ID
            meta: agent_meta 字典（七字段：agent_id、agent_type、status、
                usage、output_file、promoted、updated_at）

        Returns:
            True 更新成功，False 表示会话不存在
        """
        now = datetime.now().isoformat()
        meta_json = json.dumps(meta, ensure_ascii=False, default=str)
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE sessions SET agent_meta = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (meta_json, now, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def merge_session_agent_meta(self, session_id: str, partial: dict) -> bool:
        """合并更新子会话代理元数据（读改写在本方法锁内原子完成）。

        Args:
            session_id: 会话 ID
            partial: 要合并进 agent_meta 的部分字段

        Returns:
            True 更新成功，False 表示会话不存在
        """
        now = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT agent_meta FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    return False
                meta: dict = {}
                try:
                    parsed = json.loads(row["agent_meta"] or "{}")
                    if isinstance(parsed, dict):
                        meta = parsed
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                meta.update(partial)
                meta["updated_at"] = now
                conn.execute(
                    """
                    UPDATE sessions SET agent_meta = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(meta, ensure_ascii=False, default=str), now, session_id),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def list_child_sessions(self, parent_session_id: str) -> list[Session]:
        """列出某主对话会话派生的全部子会话（按 updated_at 降序）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE parent_session_id = ? AND COALESCE(origin, 'chat') = 'subagent'
                ORDER BY updated_at DESC
                """,
                (parent_session_id,),
            ).fetchall()
            counts = self._message_counts(conn)
            sessions = []
            for row in rows:
                sess = self._row_to_session(row, include_messages=False)
                if row["id"] in counts:
                    sess.message_count = counts[row["id"]]
                elif row["migrated_at"] or 0:
                    sess.message_count = 0
                sessions.append(sess)
            return sessions
        finally:
            conn.close()

    def list_terminal_subagent_sessions(self, limit: int = 100) -> list[Session]:
        """列出全部子代理子会话（历史重建用，按 updated_at 降序取前 limit 条）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE COALESCE(origin, 'chat') = 'subagent'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            counts = self._message_counts(conn)
            sessions = []
            for row in rows:
                sess = self._row_to_session(row, include_messages=False)
                if row["id"] in counts:
                    sess.message_count = counts[row["id"]]
                elif row["migrated_at"] or 0:
                    sess.message_count = 0
                sessions.append(sess)
            return sessions
        finally:
            conn.close()

    def get_session(self, session_id: str) -> Session | None:
        """按 ID 获取单个会话（含完整 messages 反序列化）。

        Args:
            session_id: 会话 ID

        Returns:
            Session 对象，不存在返回 None
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            sess = self._row_to_session(row, include_messages=False)
            rows = self.load_messages(session_id)
            if rows:
                sess.messages = rows
            elif not (row["migrated_at"] or 0):
                # 未迁移且无行的过渡态：回退快照列（存量启动迁移前的窗口）
                sess.messages = self._parse_blob(row["messages"])
            else:
                sess.messages = rows
            sess.message_count = len(sess.messages)
            return sess
        finally:
            conn.close()

    def list_sessions(self, workspace_path: str) -> list[Session]:
        """列出指定工作区的所有会话。

        按 updated_at 降序排列，不返回 messages（太大），只返回 message_count。
        子代理子会话（origin=subagent）不混入主会话列表。

        Args:
            workspace_path: 工作区路径

        Returns:
            Session 列表（messages 为空列表，message_count 已填充）
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE workspace_path = ?
                  AND COALESCE(origin, 'chat') != 'subagent'
                ORDER BY updated_at DESC
                """,
                (workspace_path,),
            ).fetchall()
            counts = self._message_counts(conn)
            sessions = []
            for row in rows:
                sess = self._row_to_session(row, include_messages=False)
                if row["id"] in counts:
                    sess.message_count = counts[row["id"]]
                elif row["migrated_at"] or 0:
                    sess.message_count = 0
                sessions.append(sess)
            return sessions
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> bool:
        """删除会话。

        Args:
            session_id: 会话 ID

        Returns:
            True 删除成功，False 表示会话不存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                # 行式转录随行清理；附件文件在提交成功后删除
                _, file_orphans = self._replace_within(conn, session_id, [])
                conn.execute(
                    "DELETE FROM session_input WHERE session_id = ?", (session_id,)
                )
                conn.execute(
                    "DELETE FROM session_queue_state WHERE session_id = ?", (session_id,)
                )
                cur = conn.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        self._unlink_files([self._attachment_path(a) for a in file_orphans])
        return cur.rowcount > 0

    def update_session_title(self, session_id: str, title: str) -> bool:
        """更新会话标题。

        Args:
            session_id: 会话 ID
            title: 新标题

        Returns:
            True 更新成功，False 表示会话不存在
        """
        now = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE sessions SET title = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (title, now, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def update_session_pinned(self, session_id: str, pinned: bool) -> bool:
        """更新会话置顶状态。

        Args:
            session_id: 会话 ID
            pinned: 是否置顶

        Returns:
            True 更新成功，False 表示会话不存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE sessions SET pinned = ? WHERE id = ?",
                    (1 if pinned else 0, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def update_session_spec(self, session_id: str, spec_name: str) -> bool:
        """记录会话归属的 spec 目录名（胶囊卡「进展」按会话取数的数据源）。

        AI 往 .agent/specs/<名字>/ 写盘时由文件事件钩子调用。幂等：同名
        重复记录不产生写库；改判归属（换 spec）时直接覆盖。只写元信息，
        不动 updated_at——每次勾选清单都重排会话列表太吵。

        Args:
            session_id: 会话 ID
            spec_name: spec 目录名（.agent/specs/ 下一级目录名）

        Returns:
            True 本次有实际写入，False 表示无变化或会话不存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE sessions SET spec_name = ?
                    WHERE id = ? AND (spec_name IS NULL OR spec_name != ?)
                    """,
                    (spec_name, session_id, spec_name),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_session_spec(self, session_id: str) -> str | None:
        """读取会话归属的 spec 目录名，未记录或会话不存在返回 None。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT spec_name FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            return row["spec_name"] or None
        finally:
            conn.close()

    def set_session_last_turn(self, session_id: str, meta: dict) -> bool:
        """记录最近一回合的退出信息（JSON 整段覆盖），同时刷新 updated_at。

        Args:
            session_id: 会话 ID
            meta: 退出信息字典（reason/error/finished_at/user_ts，
                user_ts 可为缺失——归属确认未通过时不写该键）

        Returns:
            True 更新成功，False 表示会话不存在
        """
        now = datetime.now().isoformat()
        meta_json = json.dumps(meta, ensure_ascii=False, default=str)
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE sessions SET last_turn = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (meta_json, now, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_session_last_turn(self, session_id: str | None) -> dict:
        """单列读取会话的最近回合退出信息（不反序列化 messages 大字段）。

        会话不存在、未装载查看会话（session_id 为 None）或坏值均返回 {}。
        """
        if not session_id:
            return {}
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT last_turn FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None or not row["last_turn"]:
                return {}
            try:
                parsed = json.loads(row["last_turn"])
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        finally:
            conn.close()

    def save_messages(
        self, session_id: str, messages: list[dict]
    ) -> bool:
        """[过渡壳] 旧整表接口：委托行式整批重建，保证单一真相源。

        写入方全面换轨后删除（阶段二 2.3）；调用方无需改动。
        返回 False 仅当会话不存在。
        """
        created = self.replace_messages_tx(session_id, messages)
        conn = self._get_conn()
        try:
            exists = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return exists is not None
        finally:
            conn.close()

    def export_transcript(
        self, session_id: str, messages: list[dict]
    ) -> str:
        """把会话全量消息导出为 JSONL 转录（压缩逃生门）。

        与会话库同根（~/.agent/transcripts/<session_id>.jsonl），覆盖式全量
        重写；供压缩续写消息引用，模型/用户可回查被边界移出活跃窗口的细节。

        Returns:
            转录文件路径字符串
        """
        transcript_dir = self.db_path.parent / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        path = transcript_dir / f"{session_id}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False, default=str))
                f.write("\n")
        return str(path)

    # ------------------------------------------------------------------
    # 行式转录：message/part/attachment + 事务写入通道
    #
    # 设计要点：
    # - 顺序是存储坐标（sequence 列，整批重建时按列表位置重赋），读写不依赖
    #   列表位置或追加时序，崩溃只会停在"某一完整批次"，不产生组内错序；
    # - 图片字节内容寻址落盘（同图一份文件），行/part 只存引用；
    # - 所有落库经 replace_messages_tx / promote_inputs_tx 单事务整批重建，
    #   天然幂等（同一列表连续调用两次行数与内容不变）；
    # - load_messages 返回"工作态"：image_ref 已还原为 data URL，
    #   与改造前从快照列读出的形态逐字段兼容。
    # ------------------------------------------------------------------

    ATTACHMENT_DIRNAME = "attachments"

    def _attachments_dir(self) -> Path:
        return self.db_path.parent / self.ATTACHMENT_DIRNAME

    def _attachment_path(self, attachment_id: str) -> Path:
        return self._attachments_dir() / attachment_id

    @staticmethod
    def _parse_data_url(url: object) -> tuple[str, str] | None:
        """解析 data URL 为 (mime, base64)；形态不符或无法解码返回 None（原样保留）。

        测试与极端输入可能携带 padding 不全的伪 base64——外部化只为真实
        图片服务，解不开的字节串留在原位，不抛错不丢块。
        """
        if not isinstance(url, str) or not url.startswith("data:"):
            return None
        head, _, body = url[5:].partition(",")
        if not body:
            return None
        try:
            base64.b64decode(body, validate=False)
        except Exception:
            return None
        mime = head.split(";", 1)[0] or "image/png"
        return mime, body

    def _store_attachment(self, conn: sqlite3.Connection, session_id: str, mime: str, b64: str) -> str:
        """附件落盘（内容哈希命名，文件已存在则跳过），并登记本会话引用行。"""
        attachment_id = hashlib.sha1(b64.encode("utf-8")).hexdigest()[:32]
        path = self._attachment_path(attachment_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(b64, validate=False))
        conn.execute(
            "INSERT OR IGNORE INTO attachment (id, session_id, mime, path, bytes, time_created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (attachment_id, session_id, mime, str(path), (len(b64) * 3) // 4, int(time.time() * 1000)),
        )
        return attachment_id

    def _encode_message_row(
        self, conn: sqlite3.Connection, session_id: str, msg: dict
    ) -> tuple[dict, list[tuple[str, dict]]]:
        """工作态消息 dict → 存储行内容 + 部件规格；不修改入参 dict。

        content 的 image_url 块与 _attachments 的 data_url 项都外部化为
        附件引用（image_ref 块 / _attachments 引用项），同时产出对应
        part 行（宿主内定位，kind=image）。已是引用形态的原样保留。
        """
        stored = dict(msg)
        parts_spec: list[tuple[str, dict]] = []
        content = stored.get("content")
        if isinstance(content, list):
            blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    parsed = self._parse_data_url((block.get("image_url") or {}).get("url"))
                    if parsed is not None:
                        mime, b64 = parsed
                        aid = self._store_attachment(conn, session_id, mime, b64)
                        blocks.append({"type": "image_ref", "attachment_id": aid, "mime": mime})
                        parts_spec.append(("image", {"slot": "content", "attachment_id": aid, "mime": mime}))
                        continue
                blocks.append(block)
            stored["content"] = blocks
        att = stored.get("_attachments")
        if isinstance(att, list):
            refs = []
            for item in att:
                if isinstance(item, dict) and item.get("data_url"):
                    parsed = self._parse_data_url(item["data_url"])
                    if parsed is not None:
                        mime, b64 = parsed
                        aid = self._store_attachment(conn, session_id, mime, b64)
                        refs.append({"attachment_id": aid, "mime": mime})
                        parts_spec.append(("image", {"slot": "attachments", "attachment_id": aid, "mime": mime}))
                        continue
                refs.append(item)
            stored["_attachments"] = refs
        return stored, parts_spec

    def _replace_within(
        self, conn: sqlite3.Connection, session_id: str, messages: list[dict]
    ) -> tuple[list[str], list[str]]:
        """在调用方事务内整批重建该会话行：DELETE 全部 → 按序 INSERT。

        返回 (message 行主键列表[与入参同序], 本次判定为孤儿的附件 id)；
        附件行的删除在事务内完成，对应文件的 unlink 由调用方在提交成功后执行
        （回滚不得删文件）。
        """
        old_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM attachment WHERE session_id = ?", (session_id,)
            ).fetchall()
        }
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM part WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM message WHERE session_id = ?", (session_id,))
        now_ms = int(time.time() * 1000)
        created_ids: list[str] = []
        for seq, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            stored, parts_spec = self._encode_message_row(conn, session_id, msg)
            mid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO message (id, session_id, role, sequence, data, time_created, time_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    mid,
                    session_id,
                    str(stored.get("role", "")),
                    seq,
                    json.dumps(stored, ensure_ascii=False, default=str),
                    now_ms,
                    now_ms,
                ),
            )
            for pseq, (kind, pdata) in enumerate(parts_spec):
                conn.execute(
                    "INSERT INTO part (id, message_id, session_id, kind, sequence, data, time_created, time_updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        mid,
                        session_id,
                        kind,
                        pseq,
                        json.dumps(pdata, ensure_ascii=False),
                        now_ms,
                        now_ms,
                    ),
                )
            created_ids.append(mid)
        new_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM attachment WHERE session_id = ?", (session_id,)
            ).fetchall()
        }
        orphaned = sorted(old_ids - new_ids)
        file_orphans: list[str] = []
        for aid in orphaned:
            still = conn.execute(
                "SELECT 1 FROM attachment WHERE id = ? AND session_id != ?", (aid, session_id)
            ).fetchone()
            conn.execute("DELETE FROM attachment WHERE id = ? AND session_id = ?", (aid, session_id))
            if still is None:
                file_orphans.append(aid)
        return created_ids, file_orphans

    @staticmethod
    def _unlink_files(paths: list[Path]) -> None:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def replace_messages_tx(self, session_id: str, messages: list[dict]) -> list[str]:
        """整批重建会话消息行（单事务），同时刷新 updated_at。

        传入列表即完整目标态：同一列表连续调用两次行数与内容不变。
        不修改调用方持有的 dict；孤儿附件文件在事务提交成功后删除。
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                created, orphan_aids = self._replace_within(conn, session_id, messages)
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), session_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        self._unlink_files([self._attachment_path(a) for a in orphan_aids])
        return created

    def load_messages(self, session_id: str) -> list[dict]:
        """读会话消息行并还原为工作态（image_ref → data URL；缺文件降级占位）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT data FROM message WHERE session_id = ? ORDER BY sequence", (session_id,)
            ).fetchall()
        finally:
            conn.close()

        out: list[dict] = []
        for r in rows:
            try:
                msg = json.loads(r["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(msg, dict):
                out.append(self._decode_message_row(msg))
        return out

    def _decode_message_row(self, stored: dict) -> dict:
        msg = dict(stored)
        content = msg.get("content")
        if isinstance(content, list):
            blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_ref":
                    resolved = self._attachment_data_url(block.get("attachment_id", ""), block.get("mime", ""))
                    if resolved is None:
                        blocks.append({"type": "text", "text": "[图片文件缺失]"})
                    else:
                        blocks.append({"type": "image_url", "image_url": {"url": resolved}})
                else:
                    blocks.append(block)
            msg["content"] = blocks
        att = msg.get("_attachments")
        if isinstance(att, list):
            items = []
            for item in att:
                if isinstance(item, dict) and item.get("attachment_id"):
                    resolved = self._attachment_data_url(item["attachment_id"], item.get("mime", ""))
                    items.append(
                        {"mime": item.get("mime", ""), "data_url": resolved or "", "missing": resolved is None}
                    )
                else:
                    items.append(item)
            msg["_attachments"] = items
        return msg

    def _attachment_data_url(self, attachment_id: str, mime: str) -> str | None:
        path = self._attachment_path(attachment_id)
        if not path.is_file():
            return None
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return None
        return f"data:{mime or 'image/png'};base64,{b64}"

    # ------------------------------------------------------------------
    # 队列转正事务通道（转正与消息落库同事务，判据挂业务持久键 _input_id）
    # ------------------------------------------------------------------

    def peek_queued_inputs(self, session_id: str) -> list[dict]:
        """按序取回 queued 行消息视图（不翻状态），翻转正由 promote_inputs_tx 落库时完成。

        auto_drain 关时返回空——暂停门与既有 promote_queued_inputs 同位。
        消息形态与转正产物一致：{**payload, _ts, _input_id, _kind[, _steer]}。
        """
        if not self.get_queue_state(session_id)["auto_drain"]:
            return []
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM session_input "
                "WHERE session_id = ? AND status = 'queued' "
                "ORDER BY CASE WHEN delivery = 'guide' THEN 0 ELSE 1 END, "
                "admitted_sequence ASC",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
        now = time.time() * 1000
        out: list[dict] = []
        for row in rows:
            payload = json.loads(row["payload"])
            msg = {**payload, "_ts": now, "_input_id": row["id"], "_kind": row["kind"]}
            if row["delivery"] == "guide":
                msg["_steer"] = True
            out.append(msg)
        return out

    def promote_inputs_tx(
        self,
        session_id: str,
        full_messages: list[dict],
        promoted_row_ids: list[str] | None = None,
        discarded_row_ids: list[str] | None = None,
    ) -> None:
        """转正事务：整批重建消息行 + 队列行翻 promoted（判据=队列行 id）。

        promoted_row_ids 缺省时从 full_messages 中携带 _input_id 的行自动
        推导（与「传入完整目标列表」语义一致，调用方无需传两份）。
        promoted_message_id 写业务持久键 _input_id（等于队列行 id），
        抗整批重建的行主键失效；编辑重发截断把被删区间的 promoted 行
        经 discarded_row_ids 同事务翻 discarded/truncated，使主动删除与
        崩溃丢失在对账判据上可区分。任一步失败整体回滚：不存在
        「promoted 而消息不在库」的终态。
        """
        if promoted_row_ids is None:
            promoted_row_ids = [
                str(m["_input_id"]) for m in full_messages
                if isinstance(m, dict) and m.get("_input_id")
            ]
        now_ms = int(time.time() * 1000)
        with self._lock:
            conn = self._get_conn()
            file_orphans: list[str] = []
            try:
                conn.execute("BEGIN IMMEDIATE")
                # 认领转正：行级互斥沿用「先翻者胜」。认领失败的行按当前状态
                # 分流——仍是 queued（已被撤回/撤销）→ 消息从待重建列表剔除；
                # 已是 promoted/discarded（历史转正行随完整列表再次进入本事务，
                # 或截断终态行）→ 保留消息，不误伤既有历史
                lost: set[str] = set()
                for rid in promoted_row_ids:
                    cur = conn.execute(
                        "UPDATE session_input SET status='promoted', "
                        "promoted_sequence=(SELECT admitted_sequence FROM session_input WHERE id=?), "
                        "promoted_message_id=?, time_updated=? "
                        "WHERE id=? AND status='queued'",
                        (rid, rid, now_ms, rid),
                    )
                    if cur.rowcount == 0:
                        state = conn.execute(
                            "SELECT status FROM session_input WHERE id=?", (rid,)
                        ).fetchone()
                        if state is None or state["status"] == "cancelled":
                            lost.add(rid)
                if lost:
                    full_messages = [
                        m for m in full_messages
                        if not (isinstance(m, dict) and m.get("_input_id") in lost)
                    ]
                _, file_orphans = self._replace_within(conn, session_id, full_messages)
                for did in discarded_row_ids or []:
                    conn.execute(
                        "UPDATE session_input SET status='discarded', status_reason='truncated', "
                        "time_updated=? WHERE id=? AND status='promoted'",
                        (now_ms, did),
                    )
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), session_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        self._unlink_files([self._attachment_path(a) for a in file_orphans])

    def promote_queued_inputs(self, session_id: str) -> list[dict]:
        """[遗留通道] 直接翻状态并返回消息——保留给无需同批落库的观测路径，
        新代码一律走 peek + promote_inputs_tx（转正与落库同事务）。
        """
        if not self.get_queue_state(session_id)["auto_drain"]:
            return []
        now = time.time() * 1000
        out: list[dict] = []
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM session_input "
                    "WHERE session_id = ? AND status = 'queued' "
                    "ORDER BY CASE WHEN delivery = 'guide' THEN 0 ELSE 1 END, "
                    "admitted_sequence ASC",
                    (session_id,),
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload"])
                    conn.execute(
                        "UPDATE session_input SET status='promoted', "
                        "promoted_sequence=?, promoted_message_id=?, time_updated=? "
                        "WHERE id=?",
                        (row["admitted_sequence"], row["id"], now, row["id"]),
                    )
                    msg = {**payload, "_ts": now, "_input_id": row["id"], "_kind": row["kind"]}
                    if row["delivery"] == "guide":
                        msg["_steer"] = True
                    out.append(msg)
                conn.commit()
            finally:
                conn.close()
        return out

    def consume_for_wake(self, session_id: str) -> tuple[list[dict], list[str]]:
        """唤醒消费：原子翻 promoted 并返回消息与行 id（合并唤起的专用通道）。

        与起轮/边界转正不同：多行文本合并为一条唤起 prompt，原行 id 无法
        逐行挂到消息 `_input_id`——行打 `status_reason='merged_wake'` 标记，
        合并 prompt 消息携带 `_merged_input_ids` 承载行 id 集合；对账与审计④
        据此把「合并承载」计入判据，不误判为丢失补写。崩溃窗口（消费后、
        起轮落库前）由启动对账按 payload 补写自愈。

        暂停门：auto_drain 关（manual/error/stopped）时表行不消费、原样留
        queued——与起轮/边界两个转正点位共享同一道门（被暂停的会话不得被
        后台通知强行唤起并吞掉队列行）；内存回退队列的门语义在 notify 层
        对齐基线。
        """
        if not self.get_queue_state(session_id)["auto_drain"]:
            return [], []
        now = time.time() * 1000
        out: list[dict] = []
        ids: list[str] = []
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM session_input "
                    "WHERE session_id = ? AND status = 'queued' "
                    "ORDER BY CASE WHEN delivery = 'guide' THEN 0 ELSE 1 END, "
                    "admitted_sequence ASC",
                    (session_id,),
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload"])
                    conn.execute(
                        "UPDATE session_input SET status='promoted', "
                        "promoted_sequence=?, promoted_message_id=?, "
                        "status_reason='merged_wake', time_updated=? "
                        "WHERE id=?",
                        (row["admitted_sequence"], row["id"], now, row["id"]),
                    )
                    msg = {**payload, "_ts": now, "_input_id": row["id"], "_kind": row["kind"]}
                    if row["delivery"] == "guide":
                        msg["_steer"] = True
                    out.append(msg)
                    ids.append(row["id"])
                conn.commit()
            finally:
                conn.close()
        return out, ids

    def take_input_for_now(self, row_id: str) -> dict | None:
        """单行取出（空闲立即/恢复起队尾行共用）：只返回消息视图，不翻状态。

        状态翻转与落库合并进该行的起轮事务（promote_inputs_tx），
        杜绝「取走即 promoted、落库靠后」的吞消息窗口。
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM session_input WHERE id = ? AND status = 'queued'", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        msg = {**json.loads(row["payload"]), "_ts": time.time() * 1000, "_input_id": row_id}
        if row["delivery"] == "guide":
            msg["_steer"] = True
        return msg

    def get_promoted_input(self, row_id: str) -> dict | None:
        """按 id 取队列行观测视图（对账与单测断言用）。"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM session_input WHERE id = ?", (row_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "kind": row["kind"],
            "delivery": row["delivery"],
            "status": row["status"],
            "status_reason": row["status_reason"],
            "promoted_message_id": row["promoted_message_id"],
            "promoted_sequence": row["promoted_sequence"],
        }

    # ------------------------------------------------------------------
    # 存量迁移与启动对账
    # ------------------------------------------------------------------

    def migrate_legacy_blobs(self) -> int:
        """把未迁移会话的整表快照重建为行表（幂等，可崩溃重跑）。

        迁移顺序：读 blob → 悬空清洗 → 单事务（重建行 + 原值转存
        messages_backup + 清 messages 列 + 打 migrated_at）。已迁移会话
        （migrated_at>0）跳过；半路崩溃遗留的半截行由重跑整批重建覆盖。
        """
        from query.utils.messages import sanitize_dangling_tool_calls

        migrated = 0
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, messages FROM sessions WHERE COALESCE(migrated_at, 0) = 0 "
                "AND id NOT IN (SELECT DISTINCT session_id FROM message)"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            sid = row["id"]
            # 清洗也在单会话防护内：脏数据不得炸穿启动迁移（单会话失败仅日志）
            try:
                msgs = json.loads(row["messages"] or "[]")
                repaired = sanitize_dangling_tool_calls([m for m in msgs if isinstance(m, dict)])
            except Exception:
                logger.warning("会话 %s 快照解析/清洗失败，按空历史迁移保底", sid, exc_info=True)
                repaired = []
            with self._lock:
                conn = self._get_conn()
                file_orphans: list[str] = []
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    # 重跑先清半截（行表若已有该会话数据一律以快照为准重建）
                    _, file_orphans = self._replace_within(conn, sid, repaired)
                    conn.execute(
                        "UPDATE sessions SET messages_backup = messages, messages = '[]', "
                        "migrated_at = ? WHERE id = ? AND COALESCE(migrated_at, 0) = 0",
                        (int(time.time() * 1000), sid),
                    )
                    conn.commit()
                    migrated += 1
                except Exception:
                    conn.rollback()
                    logger.warning("会话 %s 行式迁移失败（保留快照列，下次启动重跑）", sid, exc_info=True)
                finally:
                    conn.close()
            self._unlink_files([self._attachment_path(a) for a in file_orphans])
        return migrated

    def reconcile_promoted_inputs(self) -> int:
        """启动对账：promoted 行消息不在库 → 区分存量旧口径与真丢失后自愈。

        判据挂业务持久键：队列行 id 应出现在该会话消息行的 _input_id 集合。
        - 命中 _input_id → 无动作；
        - 旧格式行（promoted_message_id 为 _ts 值）以消息 _ts 命中 → 给该消息行
          打补 _input_id 并整批重建回写、同步规范化 promoted_message_id
          ——两条判据都稳定后，二次启动复跑零动作（幂等）；
        - 两条判据都不命中 → 真丢失：按 payload 补写消息（带 _input_id 与
          _reconciled 标记），promoted_message_id 挂行 id、status_reason='reconciled'。
        主动截断行已翻 discarded 不在扫描面（主动删除不复活）。
        """
        healed = 0
        conn = self._get_conn()
        try:
            promoted = conn.execute(
                "SELECT id, session_id, kind, payload, promoted_message_id "
                "FROM session_input WHERE status='promoted'"
            ).fetchall()
        finally:
            conn.close()
        if not promoted:
            return 0

        per_session: dict[str, list[sqlite3.Row]] = {}
        for row in promoted:
            per_session.setdefault(row["session_id"], []).append(row)

        for sid, rows in per_session.items():
            existing = self._session_row_ids(sid)
            if not existing:
                # 未迁移/无行会话不处理：行表迁移成功后下轮启动自然纳入对账
                continue
            msgs = self.load_messages(sid)
            # 判据集合：_input_id（逐行转正）+ _merged_input_ids（唤醒合并承载，
            # 一条唤起 prompt 承载多行内容，行 id 挂在合并消息的该字段）
            input_ids: set[str] = set()
            for m in msgs:
                if m.get("_input_id"):
                    input_ids.add(str(m["_input_id"]))
                for mid in m.get("_merged_input_ids") or []:
                    input_ids.add(str(mid))
            ts_map = {str(m.get("_ts")): m for m in msgs}
            additions: list[dict] = []
            renames: list[str] = []  # 需要规范化 promoted_message_id 的行
            patched = False
            for r in rows:
                if r["id"] in input_ids:
                    continue
                pmid = str(r["promoted_message_id"] or "")
                if pmid and pmid in ts_map:
                    # 存量旧格式命中：消息确实在库，只补挂业务键使其抗重建
                    ts_map[pmid]["_input_id"] = r["id"]
                    input_ids.add(r["id"])
                    renames.append(r["id"])
                    patched = True
                else:
                    payload = json.loads(r["payload"])
                    additions.append(
                        {
                            **payload,
                            "_ts": time.time() * 1000,
                            "_input_id": r["id"],
                            "_kind": r["kind"],
                            "_reconciled": True,
                        }
                    )
                    renames.append(r["id"])
            if not (additions or patched):
                continue
            self.replace_messages_tx(sid, [*msgs, *additions])
            with self._lock:
                conn = self._get_conn()
                try:
                    now_ms = int(time.time() * 1000)
                    # 规范化 promoted_message_id（旧 _ts 命中与补写行共用）
                    for rid in renames:
                        conn.execute(
                            "UPDATE session_input SET promoted_message_id=?, time_updated=? "
                            "WHERE id=? AND status='promoted'",
                            (rid, now_ms, rid),
                        )
                    for add in additions:
                        conn.execute(
                            "UPDATE session_input SET status_reason='reconciled', time_updated=? "
                            "WHERE id=? AND status='promoted'",
                            (now_ms, add["_input_id"]),
                        )
                    conn.commit()
                finally:
                    conn.close()
            if additions:
                healed += len(additions)
                logger.info("会话 %s 对账补写 %d 条转正未落库消息", sid, len(additions))
        return healed

    def _session_row_ids(self, session_id: str) -> list[str]:
        """该会话 message 行数探测（迁移标志的轻量替代：有行即已进行式存储）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT 1 FROM message WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchall()
            return ["x"] if rows else []
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 工作区 CRUD
    # ------------------------------------------------------------------

    def list_workspaces(self) -> list[Workspace]:
        """列出所有工作区。

        按置顶优先、添加时间（created_at）降序排列（同值按 rowid 降序兜底），
        顺序不随切换/使用变化；last_used_at 仅作展示，不参与排序。
        包含每个工作区下的会话数量。

        Returns:
            Workspace 列表
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT w.*,
                       (SELECT COUNT(*) FROM sessions s
                        WHERE s.workspace_path = w.path
                          AND COALESCE(s.origin, 'chat') != 'subagent') as session_count
                FROM workspaces w
                ORDER BY w.pinned DESC, w.created_at DESC, w.rowid DESC
                """
            ).fetchall()
            return [
                self._row_to_workspace(row, row["session_count"]) for row in rows
            ]
        finally:
            conn.close()

    def add_workspace(self, path: str) -> Workspace:
        """添加工作区。如已存在则不重复添加。

        Args:
            path: 工作区路径

        Returns:
            Workspace 对象
        """
        now = datetime.now().isoformat()
        name = os.path.basename(path)

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO workspaces (path, name, last_used_at, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (path, name, now, now),
                )
                conn.commit()
            finally:
                conn.close()

        return Workspace(
            path=path, name=name, last_used_at=now, session_count=0, created_at=now
        )

    def update_workspace_last_used(self, path: str) -> None:
        """更新工作区的最后使用时间。

        Args:
            path: 工作区路径
        """
        now = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    UPDATE workspaces SET last_used_at = ?
                    WHERE path = ?
                    """,
                    (now, path),
                )
                conn.commit()
            finally:
                conn.close()

    def get_last_used_workspace_path(self) -> str | None:
        """返回最近使用（last_used_at 最大）的工作区路径，空库返回 None。

        独立单查询、不依赖 list_workspaces 的展示顺序：启动恢复的
        「恢复最后使用者」语义与列表排序（置顶优先/添加时间）解耦，
        排序调整不影响恢复目标。
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT path FROM workspaces ORDER BY last_used_at DESC LIMIT 1"
            ).fetchone()
            return row["path"] if row else None
        finally:
            conn.close()

    def update_workspace_pinned(self, path: str, pinned: bool) -> bool:
        """更新工作区置顶状态。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE workspaces SET pinned = ? WHERE path = ?",
                    (1 if pinned else 0, path),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def update_workspace_alias(self, path: str, alias: str) -> bool:
        """更新工作区别名（显示 alias||name，用于区分同名项目）。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE workspaces SET alias = ? WHERE path = ?",
                    (alias, path),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def delete_workspace(self, path: str) -> bool:
        """删除工作区及其所有会话。

        Args:
            path: 工作区路径

        Returns:
            是否删除成功
        """
        with self._lock:
            conn = self._get_conn()
            try:
                # 行式转录随行清理
                sid_rows = conn.execute(
                    "SELECT id FROM sessions WHERE workspace_path = ?", (path,)
                ).fetchall()
                for r in sid_rows:
                    conn.execute("DELETE FROM part WHERE session_id = ?", (r["id"],))
                    conn.execute("DELETE FROM message WHERE session_id = ?", (r["id"],))
                    conn.execute("DELETE FROM attachment WHERE session_id = ?", (r["id"],))
                    conn.execute("DELETE FROM session_input WHERE session_id = ?", (r["id"],))
                    conn.execute("DELETE FROM session_queue_state WHERE session_id = ?", (r["id"],))
                # 再删该工作区下的所有会话
                conn.execute(
                    "DELETE FROM sessions WHERE workspace_path = ?",
                    (path,),
                )
                # 再删工作区记录
                cur = conn.execute(
                    "DELETE FROM workspaces WHERE path = ?",
                    (path,),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 任务分组 CRUD
    # ------------------------------------------------------------------

    def list_task_groups(self) -> list[TaskGroup]:
        """列出所有自定义任务分组，按创建时间升序。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM task_groups ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_task_group(row) for row in rows]
        finally:
            conn.close()

    def create_task_group(self, name: str, color: str = "") -> TaskGroup:
        """创建任务分组。

        Args:
            name: 分组名称（调用方保证非空）
            color: 颜色标识，可留空

        Returns:
            新建的 TaskGroup 对象
        """
        now = datetime.now().isoformat()
        group_id = str(uuid.uuid4())

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO task_groups (id, name, color, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (group_id, name, color, now),
                )
                conn.commit()
            finally:
                conn.close()

        return TaskGroup(id=group_id, name=name, color=color, created_at=now)

    def update_task_group(self, group_id: str, name: str | None = None, color: str | None = None) -> bool:
        """更新任务分组（重命名 / 改颜色），None 表示不修改该字段。

        Returns:
            True 更新成功，False 表示分组不存在
        """
        with self._lock:
            conn = self._get_conn()
            try:
                if name is not None:
                    cur = conn.execute(
                        "UPDATE task_groups SET name = ? WHERE id = ?",
                        (name, group_id),
                    )
                    if cur.rowcount == 0:
                        return False
                if color is not None:
                    cur = conn.execute(
                        "UPDATE task_groups SET color = ? WHERE id = ?",
                        (color, group_id),
                    )
                    if cur.rowcount == 0:
                        return False
                conn.commit()
                return True
            finally:
                conn.close()

    def delete_task_group(self, group_id: str) -> bool:
        """删除任务分组。

        事务内先把成员任务的 group_id 置空（回"未分组"）再删分组，
        避免留下指向已删分组的孤儿 group_id；不删除任何任务记录。
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE sessions SET group_id = '' WHERE group_id = ?",
                    (group_id,),
                )
                cur = conn.execute(
                    "DELETE FROM task_groups WHERE id = ?", (group_id,)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def update_session_group(self, session_id: str, group_id: str) -> bool:
        """更新任务所属分组。

        Args:
            session_id: 目标任务 id
            group_id: 目标分组 id，空串表示移出分组（回未分组）；
                非空时必须是已存在的分组，否则返回 False 且不改数据

        Returns:
            True 更新成功
        """
        with self._lock:
            conn = self._get_conn()
            try:
                if group_id:
                    exists = conn.execute(
                        "SELECT 1 FROM task_groups WHERE id = ?", (group_id,)
                    ).fetchone()
                    if exists is None:
                        return False
                cur = conn.execute(
                    "UPDATE sessions SET group_id = ? WHERE id = ?",
                    (group_id, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 分组查询
    # ------------------------------------------------------------------

    def list_all_sessions_grouped(self) -> list[tuple[Workspace, list[Session]]]:
        """按工作区分组返回所有会话。

        先一次性查出所有工作区（置顶优先、created_at 降序，同值按 rowid
        降序兜底，顺序不随使用变化），再对每个工作区查其名下的会话
        （按 updated_at 降序，不含 messages，含 message_count）。

        Returns:
            列表元素为 (工作区, 会话列表) 元组；会话列表可能为空
        """
        conn = self._get_conn()
        try:
            # 查所有工作区，置顶优先、添加时间降序（与 list_workspaces 同序）
            ws_rows = conn.execute(
                "SELECT * FROM workspaces ORDER BY pinned DESC, created_at DESC, rowid DESC"
            ).fetchall()
            workspaces = [self._row_to_workspace(row) for row in ws_rows]

            result: list[tuple[Workspace, list[Session]]] = []
            for ws in workspaces:
                # 查该工作区下的会话，按更新时间降序，不反序列化 messages；
                # 子代理子会话不混入分组视图
                session_rows = conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE workspace_path = ?
                      AND COALESCE(origin, 'chat') != 'subagent'
                    ORDER BY updated_at DESC
                    """,
                    (ws.path,),
                ).fetchall()
                counts = self._message_counts(conn)
                sessions = []
                for row in session_rows:
                    sess = self._row_to_session(row, include_messages=False)
                    if row["id"] in counts:
                        sess.message_count = counts[row["id"]]
                    elif row["migrated_at"] or 0:
                        sess.message_count = 0
                    sessions.append(sess)
                result.append((ws, sessions))
            return result
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # session_input 统一输入队列（运行中用户消息 / 后台通知同表排队转正）
    # ------------------------------------------------------------------

    def admit_session_input(
        self, session_id: str, kind: str, delivery: str, payload: dict
    ) -> str:
        """入队一行（status=queued），返回队列 id。

        admitted_sequence 在会话内递增，转正严格按该序，多来源公平混排。
        """
        row_id = f"queue_{uuid.uuid4().hex}"
        now = time.time() * 1000
        with self._lock:
            conn = self._get_conn()
            try:
                seq = conn.execute(
                    "SELECT COALESCE(MAX(admitted_sequence), -1) + 1 "
                    "FROM session_input WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO session_input "
                    "(id, session_id, kind, delivery, payload, admitted_sequence, "
                    "status, time_created, time_updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (
                        row_id,
                        session_id,
                        kind,
                        delivery,
                        json.dumps(payload, ensure_ascii=False),
                        seq,
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return row_id

    def count_queued_inputs(self, session_id: str, kind: str | None = None) -> int:
        """未转正行数（可按 kind 过滤，pending_count 唤醒补偿判定用）。"""
        conn = self._get_conn()
        try:
            if kind is None:
                return conn.execute(
                    "SELECT COUNT(*) FROM session_input "
                    "WHERE session_id = ? AND status = 'queued'",
                    (session_id,),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM session_input "
                "WHERE session_id = ? AND status = 'queued' AND kind = ?",
                (session_id, kind),
            ).fetchone()[0]
        finally:
            conn.close()

    def list_queued_inputs(self, session_id: str) -> list[dict]:
        """队列视图（前端展示用）：base64 图片以占位符替换，不回传大 payload。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM session_input "
                "WHERE session_id = ? AND status = 'queued' "
                "ORDER BY admitted_sequence ASC",
                (session_id,),
            ).fetchall()
            result: list[dict] = []
            for row in rows:
                content = json.loads(row["payload"]).get("content", "")
                if isinstance(content, list):
                    # parts 形态：文本块保留，image_url 换占位哨兵（与轮询脱敏同记号）
                    slim = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "image_url":
                            slim.append({"type": "image_url", "image_url": {"url": "__omitted__"}})
                        else:
                            slim.append(block)
                    content = slim
                result.append({
                    "id": row["id"],
                    "kind": row["kind"],
                    "delivery": row["delivery"],
                    "content": content,
                    "admitted_sequence": row["admitted_sequence"],
                    "time_created": row["time_created"],
                })
            return result
        finally:
            conn.close()

    def cancel_session_input(self, row_id: str, reason: str = "user_removed") -> bool:
        """撤销仅对 queued 行生效：status=cancelled + status_reason。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE session_input SET status='cancelled', status_reason=?, "
                    "time_updated=? WHERE id=? AND status='queued'",
                    (reason, time.time() * 1000, row_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 队列会话态（auto_drain / pause_reason）
    # ------------------------------------------------------------------

    def get_queue_state(self, session_id: str) -> dict:
        """读队列状态；无行即默认开（保证未暂停会话零回归）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT auto_drain, pause_reason FROM session_queue_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"auto_drain": True, "pause_reason": None}
        return {"auto_drain": bool(row["auto_drain"]), "pause_reason": row["pause_reason"]}

    def set_queue_auto_drain(self, session_id: str, auto_drain: bool, reason: str | None = None) -> None:
        """幂等 upsert 队列状态；恢复（auto_drain=1）时 reason 置空。"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO session_queue_state (session_id, auto_drain, pause_reason, time_updated) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "auto_drain=excluded.auto_drain, pause_reason=excluded.pause_reason, "
                    "time_updated=excluded.time_updated",
                    (session_id, 1 if auto_drain else 0, reason, time.time() * 1000),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 队列条目操作：取行 / 撤回 / 转向 / 排序 / 清空 / 单行取出
    # ------------------------------------------------------------------

    def get_queued_input(self, row_id: str) -> dict | None:
        """按 id 取未转正行（端点校验与 helper 定位用）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM session_input WHERE id = ? AND status = 'queued'",
                (row_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "kind": row["kind"],
            "delivery": row["delivery"],
            "payload": json.loads(row["payload"]),
            "admitted_sequence": row["admitted_sequence"],
        }

    def withdraw_queued_input(self, row_id: str) -> dict | None:
        """撤回 queued 行（编辑回切输入区）：翻状态成功才回 payload。

        与轮次边界转正共用行级互斥：竞态胜负以 UPDATE 的 queued 条件判定，
        先翻者生效，输者得 None（行已被转正/撤销/不存在），不产生部分效果。
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE session_input SET status='cancelled', "
                    "status_reason='user_withdraw', time_updated=? "
                    "WHERE id=? AND status='queued'",
                    (time.time() * 1000, row_id),
                )
                if cur.rowcount == 0:
                    return None
                row = conn.execute(
                    "SELECT payload FROM session_input WHERE id = ?", (row_id,)
                ).fetchone()
                return json.loads(row["payload"]) if row is not None else None
            finally:
                conn.commit()
                conn.close()

    def steer_input(self, row_id: str) -> str:
        """把 queued 行置为 guide 转向投递。

        返回 ok / not_queued / attachments_unsupported——带图片 parts 的行
        不可转向（对齐目标同规则），行保持 queue 原位。
        """
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT payload, delivery FROM session_input "
                    "WHERE id = ? AND status = 'queued'",
                    (row_id,),
                ).fetchone()
                if row is None:
                    return "not_queued"
                payload = json.loads(row["payload"])
                content = payload.get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "image_url" for b in content
                ):
                    return "attachments_unsupported"
                cur = conn.execute(
                    "UPDATE session_input SET delivery = 'guide', time_updated = ? "
                    "WHERE id = ? AND status = 'queued'",
                    (time.time() * 1000, row_id),
                )
                conn.commit()
                return "ok" if cur.rowcount > 0 else "not_queued"
            finally:
                conn.close()

    def reorder_input(self, row_id: str, before_id: str | None) -> bool:
        """before-插入重排：把行移到同会话目标行之前，before_id 空=追加末尾。

        仅 queued 行参与重排（已转正/取消行不动），重排后按新序连续编号，
        转正顺序即随之前移/后移。
        """
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT id FROM session_input "
                    "WHERE session_id = (SELECT session_id FROM session_input WHERE id = ?) "
                    "AND status = 'queued' ORDER BY admitted_sequence ASC",
                    (row_id,),
                ).fetchall()
                ids = [r["id"] for r in rows]
                if row_id not in ids:
                    return False
                # 目标指向自身：视作无操作（陈旧拖拽态/直调端点防御），不抛错不重排
                if before_id == row_id:
                    return True
                if before_id is not None and before_id not in ids:
                    return False
                ids.remove(row_id)
                insert_at = len(ids) if before_id is None else ids.index(before_id)
                ids.insert(insert_at, row_id)
                now = time.time() * 1000
                for seq, rid in enumerate(ids):
                    conn.execute(
                        "UPDATE session_input SET admitted_sequence = ?, time_updated = ? "
                        "WHERE id = ?",
                        (seq, now, rid),
                    )
                conn.commit()
                return True
            finally:
                conn.close()

    def clear_queued_inputs(self, session_id: str) -> int:
        """整会话清空：全部 queued 行置 cancelled+user_removed，返回条数。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE session_input SET status='cancelled', status_reason=?, "
                    "time_updated=? WHERE session_id=? AND status='queued'",
                    ("user_removed", time.time() * 1000, session_id),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def tail_queued_input(self, session_id: str) -> dict | None:
        """取该会话 admitted_sequence 最大的 queued 行（恢复起轮的 prompt 行）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM session_input "
                "WHERE session_id = ? AND status = 'queued' "
                "ORDER BY admitted_sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        return self.get_queued_input(row["id"]) if row is not None else None
