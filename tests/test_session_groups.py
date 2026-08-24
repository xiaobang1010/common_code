"""任务分组功能测试：分组 CRUD、删除分组成员回退、group_id 校验、grouped 响应字段。

store 层用临时库直连；路由层 monkeypatch server.state.session_store 后直接调用
路由函数（对齐既有测试的装配方式，不经 HTTP 栈）。
"""

from __future__ import annotations

import pytest

import server.state
from server.routers.sessions.routes import list_sessions_grouped, update_session
from server.routers.session_groups.routes import create_session_group
from session.store import SessionStore


@pytest.fixture
def store(workspace) -> SessionStore:
    """临时库的 SessionStore。"""
    return SessionStore(db_path=workspace / "sessions.db")


def test_task_group_crud(store: SessionStore):
    """创建/列表/更新（重命名与改色）全链路持久化。"""
    assert store.list_task_groups() == []

    group = store.create_task_group("调研", color="#61afef")
    assert group.name == "调研"
    assert group.color == "#61afef"

    # 按创建时间升序：先建的在前
    other = store.create_task_group("实现")
    assert [g.id for g in store.list_task_groups()] == [group.id, other.id]

    # 重命名与改色可分别生效
    assert store.update_task_group(group.id, name="调研汇总") is True
    assert store.update_task_group(group.id, color="#98c379") is True
    groups = {g.id: g for g in store.list_task_groups()}
    assert groups[group.id].name == "调研汇总"
    assert groups[group.id].color == "#98c379"
    assert groups[other.id].name == "实现"


def test_delete_group_members_fall_back_to_ungrouped(store: SessionStore):
    """删除分组：成员任务保留且 group_id 置空，无孤儿归属。"""
    session = store.create_session("D:/ws/demo", title="示例任务")
    group = store.create_task_group("临时组")

    assert store.update_session_group(session.id, group.id) is True
    deleted = store.delete_task_group(group.id)
    assert deleted is True

    kept = store.get_session(session.id)
    assert kept is not None
    assert kept.group_id == ""
    assert store.list_task_groups() == []


def test_update_session_group_validates_group_exists(store: SessionStore):
    """归到不存在的分组返回 False 且不改数据；空串表示移出分组。"""
    session = store.create_session("D:/ws/demo", title="示例任务")
    group = store.create_task_group("正常组")

    assert store.update_session_group(session.id, "not-exist") is False
    assert store.get_session(session.id).group_id == ""

    assert store.update_session_group(session.id, group.id) is True
    assert store.get_session(session.id).group_id == group.id

    # 空串 = 移出分组
    assert store.update_session_group(session.id, "") is True
    assert store.get_session(session.id).group_id == ""


def test_create_group_requires_name(workspace):
    """路由层必填校验：name 为空不建组。"""
    for body in ({}, {"name": ""}, {"name": "   "}):
        result = create_session_group(body)
        assert result["ok"] is False


def test_grouped_api_exposes_task_groups_and_group_id(workspace, monkeypatch):
    """grouped 响应透出 task_groups 与每条会话的 group_id（写入值能原样读回）。"""
    store = SessionStore(db_path=workspace / "sessions.db")
    monkeypatch.setattr(server.state, "session_store", store)

    session = store.create_session("D:/ws/demo", title="归组任务")
    ungrouped = store.create_session("D:/ws/other", title="未归组任务")
    group = store.create_task_group("跨项目主题")

    assert store.update_session_group(session.id, group.id) is True

    data = list_sessions_grouped()
    assert [g["id"] for g in data["task_groups"]] == [group.id]
    assert data["task_groups"][0]["name"] == "跨项目主题"

    group_ids = {
        s["id"]: s.get("group_id") for g in data["groups"] for s in g["sessions"]
    }
    # 归组任务读回的 group_id 与写入一致（防行转换漏读导致恒空串）
    assert group_ids[session.id] == group.id
    assert group_ids[ungrouped.id] == ""


def test_patch_session_route_group_id_validation(workspace, monkeypatch):
    """PATCH /api/sessions/{id} 的 group_id 分支：存在则更新，不存在报错不变更。"""
    store = SessionStore(db_path=workspace / "sessions.db")
    monkeypatch.setattr(server.state, "session_store", store)

    session = store.create_session("D:/ws/demo", title="示例任务")
    group = store.create_task_group("目标组")

    ok = update_session(session.id, {"group_id": group.id})
    assert ok == {"ok": True}
    assert store.get_session(session.id).group_id == group.id

    bad = update_session(session.id, {"group_id": "ghost-group"})
    assert bad.status_code == 404
    # 数据未被破坏
    assert store.get_session(session.id).group_id == group.id
