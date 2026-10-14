"""工作区列表稳定排序测试：pinned 优先、created_at 降序、切换不重排、
grouped 接口同序、旧库迁移回填幂等、启动恢复目标与列表顺序解耦。

store 层用临时库直连（对齐既有测试的装配方式）；启动恢复直接调
startup.setup.restore_last_workspace（不经 HTTP 栈）。
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from session.store import SessionStore
from startup.setup import restore_last_workspace


@pytest.fixture
def store(workspace) -> SessionStore:
    """临时库的 SessionStore。"""
    return SessionStore(db_path=workspace / "sessions.db")


WS_A = "D:/ws/proj-a"
WS_B = "D:/ws/proj-b"
WS_C = "D:/ws/proj-c"


def _add_three(store: SessionStore) -> None:
    """按 a/b/c 顺序添加三个工作区。"""
    for path in (WS_A, WS_B, WS_C):
        store.add_workspace(path)


def test_add_order_stable_newest_first(store: SessionStore):
    """未置顶按添加时间降序：后添加的在前，添加返回对象携带 created_at。"""
    added = store.add_workspace(WS_A)
    assert added.created_at, "add_workspace 返回对象应携带 created_at"
    _add_three(store)

    assert [w.path for w in store.list_workspaces()] == [WS_C, WS_B, WS_A]


def test_pin_moves_to_front_and_unpin_restores(store: SessionStore):
    """手动置顶排最前，取消置顶后回到未置顶区按添加序的既有位置。"""
    _add_three(store)

    store.update_workspace_pinned(WS_B, True)
    assert [w.path for w in store.list_workspaces()] == [WS_B, WS_C, WS_A]

    store.update_workspace_pinned(WS_B, False)
    assert [w.path for w in store.list_workspaces()] == [WS_C, WS_B, WS_A]


def test_switch_activity_does_not_reorder(store: SessionStore):
    """切换工作区/会话（bump last_used_at）不改变列表顺序，仅刷新时间。"""
    _add_three(store)
    before = [w.path for w in store.list_workspaces()]

    # 模拟反复切换：连续刷新不同工作区的 last_used_at
    for _ in range(2):
        for path in (WS_A, WS_B, WS_C):
            store.update_workspace_last_used(path)

    assert [w.path for w in store.list_workspaces()] == before
    # last_used_at 照常刷新（头部「最后活跃」显示口径不受排序改动影响）
    times = {w.path: w.last_used_at for w in store.list_workspaces()}
    assert times[WS_C] >= times[WS_A]


def test_grouped_order_matches_list_order(store: SessionStore):
    """grouped 接口的工作区顺序与 list_workspaces 完全一致。"""
    for path in (WS_A, WS_B, WS_C):
        store.add_workspace(path)
        store.create_session(path, title=f"任务-{path}")

    list_paths = [w.path for w in store.list_workspaces()]
    grouped_paths = [ws.path for ws, _ in store.list_all_sessions_grouped()]
    assert grouped_paths == list_paths


def test_migration_backfills_created_at_preserving_insertion_order(workspace):
    """旧库（无 created_at 列）启动：补列并回填，顺序=旧插入序（后插入在前）。"""
    db = workspace / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE workspaces (
            path TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            last_used_at TEXT NOT NULL,
            pinned INTEGER DEFAULT 0,
            alias TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        "INSERT INTO workspaces (path, name, last_used_at) VALUES (?, ?, ?)",
        (WS_A, "proj-a", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO workspaces (path, name, last_used_at) VALUES (?, ?, ?)",
        (WS_B, "proj-b", "2026-01-02T00:00:00"),
    )
    conn.commit()
    conn.close()

    store = SessionStore(db_path=db)
    rows = store.list_workspaces()
    # 回填为同一基值，rowid DESC tie-break：后插入的 B 在前（旧插入序保持）
    assert [w.path for w in rows] == [WS_B, WS_A]
    assert all(w.created_at for w in rows), "存量行应全部回填 created_at"

    # 幂等：重开不重复回填、结果不变
    again = SessionStore(db_path=db).list_workspaces()
    assert [(w.path, w.created_at) for w in again] == [
        (w.path, w.created_at) for w in rows
    ]


def test_restore_target_decoupled_from_list_order(store, workspace):
    """启动恢复取 last_used_at 最新者，与列表首位（置顶优先）解耦。"""
    a = workspace / "proj-a"
    b = workspace / "proj-b"
    a.mkdir()
    b.mkdir()
    store.add_workspace(str(a))
    store.add_workspace(str(b))
    store.update_workspace_last_used(str(b))
    # a 手动置顶成为列表首位，但最近使用者仍是 b
    store.update_workspace_pinned(str(a), True)
    assert store.list_workspaces()[0].path == os.path.normpath(str(a))

    assert restore_last_workspace(store) == os.path.normpath(str(b))
