"""spec 进展端点测试 — 覆盖清单解析、代码围栏排除、无 spec 降级与会话归属。

用 conftest 的 workspace fixture 把工作区切到 tmp_path，在隔离目录里
造 .agent/specs/<任务名>/ 三件套后直接调用路由函数断言解析结果。
会话归属用临时库 SessionStore 造带工具调用的消息（对齐引擎存储格式），
monkeypatch server.state.session_store 后传 session_id 断言。
"""

from __future__ import annotations

import json
import os
import time

import pytest

import server.state
from server.file_events import notify_file_changed
from server.routers.spec.routes import spec_progress
from session.store import SessionStore


@pytest.fixture
def store(workspace, monkeypatch) -> SessionStore:
    """临时库的 SessionStore，并接管路由读取的 server.state.session_store。"""
    store = SessionStore(db_path=workspace / "sessions.db")
    monkeypatch.setattr(server.state, "session_store", store)
    return store


def _tool_call_msg(path: str) -> dict:
    """构造一条带工具调用的 assistant 消息（OpenAI 格式，arguments 为 JSON 串）。"""
    args = json.dumps({"path": path, "content": "- [ ] 占位\n"}, ensure_ascii=False)
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "write_file", "arguments": args},
            }
        ],
    }


def _make_spec(workspace, name: str, tasks: str, checks: str | None) -> None:
    """在临时工作区造一个 spec 目录，checklist 传 None 表示不建该文件。"""
    spec_dir = workspace / ".agent" / "specs" / name
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# 大纲\n", encoding="utf-8")
    (spec_dir / "tasks.md").write_text(tasks, encoding="utf-8")
    if checks is not None:
        (spec_dir / "checklist.md").write_text(checks, encoding="utf-8")


def test_progress_parses_both_checklists(workspace):
    """正常解析：两份清单的 total/done/items 与文档勾选状态一致。"""
    _make_spec(
        workspace,
        "demo",
        tasks="# 任务\n\n- [x] 1.1 做完的事\n- [ ] 1.2 没做的事\n普通文本行\n",
        checks="# 验收\n\n- [x] 验收项A\n",
    )

    result = spec_progress()

    assert result["spec"]["name"] == "demo"
    assert result["spec"]["path"] == ".agent/specs/demo"
    assert result["tasks"]["total"] == 2
    assert result["tasks"]["done"] == 1
    assert result["tasks"]["items"][0] == {"text": "1.1 做完的事", "done": True}
    assert result["tasks"]["items"][1] == {"text": "1.2 没做的事", "done": False}
    assert result["checks"]["total"] == 1
    assert result["checks"]["done"] == 1


def test_progress_no_specs_returns_null(workspace):
    """工作区没有 .agent/specs/ 时返回 spec null 不报错。"""
    result = spec_progress()
    assert result == {"spec": None}


def test_progress_missing_checklist_returns_empty_group(workspace):
    """只有 tasks.md 没有 checklist.md 时，验证组为空清单。"""
    _make_spec(workspace, "solo", tasks="- [ ] 唯一任务\n", checks=None)

    result = spec_progress()

    assert result["tasks"]["total"] == 1
    assert result["checks"] == {"total": 0, "done": 0, "items": []}


def test_progress_skips_code_fence_and_non_checklist_lines(workspace):
    """代码围栏内的 checkbox 示例与非清单行都不误收。"""
    _make_spec(
        workspace,
        "fenced",
        tasks=(
            "# 标题不含条目\n"
            "## 普通小节\n"
            "1. 有序列表也不是条目\n"
            "- 普通无勾选列表行不是条目\n"
            "```\n- [x] 围栏内的示例不算\n- [ ] 围栏内也不算\n```\n"
            "~~~\n- [x] 波浪围栏同样不算\n~~~\n"
            "- [x] 围栏外的真条目\n"
        ),
        checks="",
    )

    result = spec_progress()

    assert result["tasks"]["total"] == 1
    assert result["tasks"]["items"][0]["text"] == "围栏外的真条目"
    # 空文件（只有空串内容）解析为 0 条
    assert result["checks"]["total"] == 0


def test_progress_tolerates_non_utf8_checklist(workspace):
    """清单文件编码异常（如 GBK）时按空清单降级，不抛 500。"""
    spec_dir = workspace / ".agent" / "specs" / "gbk"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# 大纲\n", encoding="utf-8")
    (spec_dir / "tasks.md").write_text("- [x] 中文任务\n", encoding="gbk")

    result = spec_progress()

    assert result["spec"]["name"] == "gbk"
    assert result["tasks"] == {"total": 0, "done": 0, "items": []}


def test_progress_picks_most_recent_spec(workspace):
    """多个 spec 并存时取目录 mtime 最新的一个（不传 session_id 的旧行为）。"""
    _make_spec(workspace, "old-spec", tasks="- [ ] 旧任务\n", checks="")
    _make_spec(workspace, "new-spec", tasks="- [ ] 新任务\n", checks="")
    old_dir = workspace / ".agent" / "specs" / "old-spec"
    new_dir = workspace / ".agent" / "specs" / "new-spec"
    old_stamp = time.time() - 600
    os.utime(old_dir, (old_stamp, old_stamp))
    os.utime(new_dir, None)

    result = spec_progress()

    assert result["spec"]["name"] == "new-spec"
    assert result["tasks"]["items"][0]["text"] == "新任务"


def test_progress_follows_session_attribution(workspace, store):
    """传 session_id 时返回该会话最后动过的 spec，同工作区不同会话各归各。"""
    _make_spec(workspace, "alpha", tasks="- [x] 甲一\n", checks="")
    _make_spec(workspace, "beta", tasks="- [ ] 乙一\n- [ ] 乙二\n", checks="")

    sess_a = store.create_session(str(workspace), title="A")
    store.save_messages(
        sess_a.id,
        [_tool_call_msg(".agent/specs/alpha/tasks.md"), _tool_call_msg(".agent/specs/beta/tasks.md")],
    )
    sess_b = store.create_session(str(workspace), title="B")
    store.save_messages(sess_b.id, [_tool_call_msg(".agent/specs/alpha/checklist.md")])

    result_a = spec_progress(session_id=sess_a.id)
    assert result_a["spec"]["name"] == "beta"
    assert result_a["tasks"]["total"] == 2

    result_b = spec_progress(session_id=sess_b.id)
    assert result_b["spec"]["name"] == "alpha"
    assert result_b["tasks"]["total"] == 1
    assert result_b["checks"] == {"total": 0, "done": 0, "items": []}


def test_progress_session_without_spec_refs_returns_null(workspace, store):
    """会话没碰过 spec 文件时返回 null，正文提过别人 spec 路径也不算归属。"""
    _make_spec(workspace, "alpha", tasks="- [ ] 一\n", checks="")
    sess = store.create_session(str(workspace), title="普通会话")
    store.save_messages(
        sess.id,
        [{"role": "user", "content": "看看 .agent/specs/alpha/ 的进展"}],
    )

    assert spec_progress(session_id=sess.id) == {"spec": None}


def test_progress_unknown_session_returns_null(workspace, store):
    """会话 id 不存在时返回 null，不回退到工作区最近活跃 spec。"""
    _make_spec(workspace, "alpha", tasks="- [ ] 一\n", checks="")

    assert spec_progress(session_id="no-such-session") == {"spec": None}


def test_progress_session_spec_dir_deleted_returns_null(workspace, store):
    """归属的 spec 目录已被删除时返回 null，不猜别的 spec。"""
    sess = store.create_session(str(workspace), title="删掉的 spec")
    store.save_messages(sess.id, [_tool_call_msg(".agent/specs/gamma/tasks.md")])

    assert spec_progress(session_id=sess.id) == {"spec": None}


def test_write_event_records_session_attribution(workspace, store):
    """任务上下文里写 .agent/specs/<名字>/ 即记归属，消息未落库也能查到进展。"""
    _make_spec(workspace, "live", tasks="- [x] 先勾一个\n", checks="")
    sess = store.create_session(str(workspace), title="跑中的任务")

    token = server.state.session_var.set(sess.id)
    try:
        target = workspace / ".agent" / "specs" / "live" / "tasks.md"
        notify_file_changed(str(target), "write", 1, 100)
    finally:
        server.state.session_var.reset(token)

    assert store.get_session_spec(sess.id) == "live"
    result = spec_progress(session_id=sess.id)
    assert result["spec"]["name"] == "live"
    assert result["tasks"]["done"] == 1


def test_write_event_without_task_context_records_nothing(workspace, store):
    """非任务上下文（如用户手改 spec 文件）不改归属。"""
    _make_spec(workspace, "human", tasks="- [ ] 一\n", checks="")
    sess = store.create_session(str(workspace), title="无任务")

    target = workspace / ".agent" / "specs" / "human" / "tasks.md"
    notify_file_changed(str(target), "write", 1, 100)

    assert store.get_session_spec(sess.id) is None


def test_recorded_spec_beats_message_scan(workspace, store):
    """归属优先级：会话行上的写盘记录优先于消息里的路径识别。"""
    _make_spec(workspace, "recorded", tasks="- [ ] 一\n", checks="")
    sess = store.create_session(str(workspace), title="改判归属")
    store.update_session_spec(sess.id, "recorded")
    # 消息里出现的是另一个 spec（如早期试建后又换方案）
    store.save_messages(sess.id, [_tool_call_msg(".agent/specs/from-messages/tasks.md")])

    result = spec_progress(session_id=sess.id)

    assert result["spec"]["name"] == "recorded"
