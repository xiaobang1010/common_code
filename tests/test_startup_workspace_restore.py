"""启动工作区恢复测试：restore_last_workspace 的命中与三类回退场景。

store 层用临时库直连（对齐既有测试的装配方式），工作区根经 conftest
的 workspace fixture 切到临时目录，测试结束自动恢复。
"""

from __future__ import annotations

import os

import pytest

from session.store import SessionStore
from startup.setup import restore_last_workspace


@pytest.fixture
def store(workspace) -> SessionStore:
    """临时库的 SessionStore。"""
    return SessionStore(db_path=workspace / "sessions.db")


def test_restore_returns_latest_workspace(store: SessionStore, workspace):
    """命中场景：返回 last_used_at 最新的工作区路径（normpath 归一口径）。"""
    older = workspace / "proj-a"
    newer = workspace / "proj-b"
    older.mkdir()
    newer.mkdir()
    store.add_workspace(str(older))
    store.add_workspace(str(newer))
    # 后更新者成为最近使用（last_used_at 降序取首位）
    store.update_workspace_last_used(str(newer))

    restored = restore_last_workspace(store)

    assert restored == os.path.normpath(str(newer))


def test_restore_falls_back_when_dir_missing(store: SessionStore, workspace):
    """回退场景：最近工作区目录已被外部删除/改名时不恢复。"""
    gone = workspace / "gone"
    store.add_workspace(str(gone))  # 只登记不建目录

    assert restore_last_workspace(store) is None


def test_restore_falls_back_on_empty_store(store: SessionStore):
    """回退场景：空库（全新安装）无可恢复目标。"""
    assert store.list_workspaces() == []
    assert restore_last_workspace(store) is None


def test_restore_does_not_touch_last_used(store: SessionStore, workspace):
    """恢复是只读动作：调用前后 workspaces 表的 last_used_at 保持不变。"""
    proj = workspace / "proj"
    proj.mkdir()
    store.add_workspace(str(proj))
    store.update_workspace_last_used(str(proj))
    before = [w.last_used_at for w in store.list_workspaces()]

    restore_last_workspace(store)

    after = [w.last_used_at for w in store.list_workspaces()]
    assert before == after
