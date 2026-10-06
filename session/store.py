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
import time
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

    def promote_queued_inputs(self, session_id: str) -> list[dict]:
        """按序转正本会话全部 queued 行，返回消息 dict（带 _ts/_input_id）。

        auto_drain=0 时直接返回空——暂停语义在 store 层统一把关，
        起轮与轮次边界两个转正点位共享同一道门。
        转正顺序：guide 行优先、组内按 admitted_sequence；guide 行转正消息
        附 `_steer`（下划线字段发模型前剥离，供前端渲染「已引导对话」标）。
        观测字段约定：promoted_sequence 沿用准入序号、promoted_message_id 取生成消息的 _ts。
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
                        (row["admitted_sequence"], str(now), now, row["id"]),
                    )
                    msg = {**payload, "_ts": now, "_input_id": row["id"]}
                    if row["delivery"] == "guide":
                        msg["_steer"] = True
                    out.append(msg)
                conn.commit()
            finally:
                conn.close()
        return out

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
    # 队列条目操作：取行 / 编辑 / 转向 / 排序 / 清空 / 单行取出
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

    def edit_queued_input(self, row_id: str, content: str) -> bool:
        """编辑仅 queued 纯文本行生效：重写 payload content 文本，保序不动。"""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT payload FROM session_input WHERE id = ? AND status = 'queued'",
                    (row_id,),
                ).fetchone()
                if row is None:
                    return False
                payload = json.loads(row["payload"])
                if not isinstance(payload.get("content"), str):
                    return False
                payload["content"] = content
                cur = conn.execute(
                    "UPDATE session_input SET payload = ?, time_updated = ? "
                    "WHERE id = ? AND status = 'queued'",
                    (json.dumps(payload, ensure_ascii=False), time.time() * 1000, row_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
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

    def take_input_for_now(self, row_id: str) -> dict | None:
        """单行取出转正（空闲立即/恢复起队尾行共用），返回该行的消息 dict。

        该行脱离 queued 集合，起轮点位的全量转正不会再碰它（防双注入）；
        guide 行同样补 `_steer`（恢复路径取到的队尾行可能已被转向）。
        """
        now = time.time() * 1000
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM session_input WHERE id = ? AND status = 'queued'",
                    (row_id,),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE session_input SET status='promoted', "
                    "promoted_sequence=?, promoted_message_id=?, time_updated=? "
                    "WHERE id=?",
                    (row["admitted_sequence"], str(now), now, row_id),
                )
                conn.commit()
            finally:
                conn.close()
        msg = {**json.loads(row["payload"]), "_ts": now, "_input_id": row_id}
        if row["delivery"] == "guide":
            msg["_steer"] = True
        return msg

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
