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
            return [self._row_to_session(row, include_messages=False) for row in rows]
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
            return [self._row_to_session(row, include_messages=False) for row in rows]
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
            return self._row_to_session(row) if row else None
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
                       (SELECT COUNT(*) FROM sessions s
                        WHERE s.workspace_path = w.path
                          AND COALESCE(s.origin, 'chat') != 'subagent') as session_count
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
                sessions = [
                    self._row_to_session(row, include_messages=False)
                    for row in session_rows
                ]
                result.append((ws, sessions))
            return result
        finally:
            conn.close()
