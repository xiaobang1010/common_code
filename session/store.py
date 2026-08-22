"""会话持久化存储层 - SQLite。

把对话历史从内存搬到磁盘，服务器重启后还能恢复。

两张表：
  - sessions: 每条记录是一次对话会话（含完整消息列表，JSON 序列化）
  - workspaces: 工作区（项目目录）登记表，一个工作区可以有多个会话

线程安全：写操作用 threading.Lock 保护，连接按操作创建（check_same_thread=False）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from session.models import Session, TaskGroup, Workspace


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
                    pinned INTEGER DEFAULT 0
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
                    alias TEXT DEFAULT ''
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

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at)"
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

        session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "group_id" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN group_id TEXT DEFAULT ''")

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
        )

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
        self, workspace_path: str, title: str = "", branch: str = ""
    ) -> Session:
        """创建新会话，生成 UUID，同时确保工作区已登记。

        Args:
            workspace_path: 工作区路径
            title: 会话标题，可留空（后续自动生成）
            branch: 创建时的 git 分支

        Returns:
            新建的 Session 对象
        """
        now = datetime.now().isoformat()
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
                        (id, workspace_path, title, branch, created_at, updated_at, messages)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        workspace_path,
                        title,
                        branch,
                        now,
                        now,
                        "[]",
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
        )

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
            return self._row_to_session(row) if row else None
        finally:
            conn.close()

    def list_sessions(self, workspace_path: str) -> list[Session]:
        """列出指定工作区的所有会话。

        按 updated_at 降序排列，不返回 messages（太大），只返回 message_count。

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
                ORDER BY updated_at DESC
                """,
                (workspace_path,),
            ).fetchall()
            return [self._row_to_session(row, include_messages=False) for row in rows]
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
                cur = conn.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

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

    def save_messages(
        self, session_id: str, messages: list[dict]
    ) -> bool:
        """保存消息列表到会话，同时更新 updated_at。

        Args:
            session_id: 会话 ID
            messages: OpenAI 格式消息列表

        Returns:
            True 保存成功，False 表示会话不存在
        """
        now = datetime.now().isoformat()
        messages_json = json.dumps(messages, ensure_ascii=False, default=str)
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE sessions SET messages = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (messages_json, now, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 工作区 CRUD
    # ------------------------------------------------------------------

    def list_workspaces(self) -> list[Workspace]:
        """列出所有工作区。

        按 last_used_at 降序排列，包含每个工作区下的会话数量。

        Returns:
            Workspace 列表
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT w.*,
                       (SELECT COUNT(*) FROM sessions s WHERE s.workspace_path = w.path) as session_count
                FROM workspaces w
                ORDER BY w.last_used_at DESC
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
                    INSERT OR IGNORE INTO workspaces (path, name, last_used_at)
                    VALUES (?, ?, ?)
                    """,
                    (path, name, now),
                )
                conn.commit()
            finally:
                conn.close()

        return Workspace(
            path=path, name=name, last_used_at=now, session_count=0
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
                # 先删该工作区下的所有会话
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

        先一次性查出所有工作区（按 last_used_at 降序），再对每个工作区
        查其名下的会话（按 updated_at 降序，不含 messages，含 message_count）。

        Returns:
            列表元素为 (工作区, 会话列表) 元组；会话列表可能为空
        """
        conn = self._get_conn()
        try:
            # 查所有工作区，按最后使用时间降序
            ws_rows = conn.execute(
                "SELECT * FROM workspaces ORDER BY last_used_at DESC"
            ).fetchall()
            workspaces = [self._row_to_workspace(row) for row in ws_rows]

            result: list[tuple[Workspace, list[Session]]] = []
            for ws in workspaces:
                # 查该工作区下的会话，按更新时间降序，不反序列化 messages
                session_rows = conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE workspace_path = ?
                    ORDER BY updated_at DESC
                    """,
                    (ws.path,),
                ).fetchall()
                sessions = [
                    self._row_to_session(row, include_messages=False)
                    for row in session_rows
                ]
                result.append((ws, sessions))
            return result
        finally:
            conn.close()
