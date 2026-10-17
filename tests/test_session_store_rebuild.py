"""存储层重建（session-store-rebuild）缺陷钉死测试。

三条复现用例钉死取证确认的缺陷形态（阶段零先红，随修复转绿）：
a) 并行读图轮：注入块夹进工具结果组 + 位置式清洗叠加 → 同 tool_call_id 双份；
b) 队列转正与落库分离：转正翻状态后落库失败即吞消息（目标 API promote_inputs_tx 同事务）；
c) 子会话直存：在途悬空形态原样落库（持久化出口必须清洗/事务）。
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from query.config import build_query_config
from query.engine import QueryEngine, build_engine_config
from query.loop import query_loop
from query.services.api.llm import StreamEvent
from query.utils.messages import sanitize_dangling_tool_calls
from tools.protocol import ToolResult, build_tool


class _EmptyInput(BaseModel):
    pass


def _image_tool():
    """模拟现状 Read 图片形态：结果正文占位 + new_messages 注入图片 parts user 块。"""

    async def _exec(inp, context):
        return ToolResult(
            content="[Attached image/png: Read image]",
            new_messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "[Attached image/png: Read image]"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }],
        )

    return build_tool(
        name="ImgTool", description="读图", input_schema=_EmptyInput,
        execute=_exec, prompt="", is_read_only=True,
    )


@dataclass
class _TwoRoundDeps:
    """第一轮发起两个并行读图调用，第二轮直接收束。"""

    calls: int = 0

    def get_uuid(self) -> str:
        return "u"

    async def call_model(self, messages=None, tools=None, **kw):
        self.calls += 1
        if self.calls == 1:
            for idx, cid in enumerate(("call_a", "call_b")):
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=idx,
                    tool_call_id=cid, tool_call_name="ImgTool",
                )
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=idx, tool_call_arguments="{}",
                )
            yield StreamEvent(type="done", finish_reason="tool_calls")
            yield StreamEvent(type="usage", usage={"total_tokens": 5})
        else:
            yield StreamEvent(type="content", content="两图已读")
            yield StreamEvent(type="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_parallel_image_round_has_no_duplicate_tool_ids():
    """a) 并行两读图轮完整跑完：落库前清洗后每个 tool_call_id 恰一条结果，组内无夹心。"""
    config = build_engine_config(model="fake", tools=[_image_tool()], max_turns=5, deps=_TwoRoundDeps())
    engine = QueryEngine(config, initial_messages=[{"role": "user", "content": "读两张图"}])
    async for _ev in query_loop(engine, build_query_config(session_id="s_img")):
        pass

    sanitized = sanitize_dangling_tool_calls(engine.mutable_messages)

    tool_ids = Counter(
        m.get("tool_call_id") for m in sanitized
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    )
    assert all(n == 1 for n in tool_ids.values()), f"存在同 id 双份结果: {dict(tool_ids)}"

    # 清洗后序列里，任何调用组（assistant+tool_calls 与其结果）内不得夹非 tool 行
    pending: set[str] = set()
    for m in sanitized:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            pending = {tc["id"] for tc in m["tool_calls"] if tc.get("id")}
        elif role == "tool":
            pending.discard(m.get("tool_call_id"))
        else:
            assert not pending, f"工具组未收齐前遇到 {role} 行（夹心）"
            pending = set()


# ---------------------------------------------------------------------------
# a2) 图片宿主绑定与协议投影
# ---------------------------------------------------------------------------


def test_expand_tool_attachments_projection():
    """持久形态→协议投影：展开行紧随宿主 tool 行、形状与旧注入行一致、幂等、缺图跳过。"""
    from query.utils.messages import expand_tool_attachments

    msgs = [
        {"role": "user", "content": "读两张图"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "[Attached image/png: Read image]",
         "_attachments": [{"mime": "image/png", "data_url": "data:image/png;base64,AAAA"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "[Attached image/png: Read image]",
         "_attachments": [{"mime": "image/png", "data_url": ""}]},  # 缺文件
        {"role": "user", "content": "继续"},
    ]
    out = expand_tool_attachments(msgs)
    # 展开行紧随 c1 宿主行；c2 缺图不展开；其余行原序保留
    assert [m.get("role") for m in out] == ["user", "assistant", "tool", "user", "tool", "user"]
    expanded = out[3]
    assert [b["type"] for b in expanded["content"]] == ["text", "image_url"]
    assert expanded["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    assert expanded["content"][0]["text"] == "[Attached image/png: Read image]"
    # 幂等：展开行不带 _attachments，重跑同输出
    assert expand_tool_attachments(out) == out
    # 不修改传入列表
    assert "_attachments" in msgs[2]


def test_build_api_request_projects_tool_attachments():
    """build_api_request 集成：宿主绑定的图片在发出的请求里投影为紧随结果行的图片 parts 行。"""
    from query.utils.api import build_api_request

    messages = [
        {"role": "user", "content": "读图"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "[Attached image/png: Read image]",
         "_attachments": [{"mime": "image/png", "data_url": "data:image/png;base64,AAAA"}]},
    ]
    request = build_api_request(messages=messages, system_prompt=[], tools=[], model="m")
    roles = [(m["role"], m.get("tool_call_id")) for m in request["messages"]]
    assert roles == [("user", None), ("assistant", None), ("tool", "c1"), ("user", None)]
    parts = request["messages"][3]["content"]
    assert [b["type"] for b in parts] == ["text", "image_url"]
    # 重复构建输出字节稳定（投影确定性）
    again = build_api_request(messages=messages, system_prompt=[], tools=[], model="m")
    assert again["messages"] == request["messages"]


# ---------------------------------------------------------------------------
# b) 转正与落库同事务
# ---------------------------------------------------------------------------


@pytest.fixture
def store_env(workspace, monkeypatch):
    """真实 SessionStore + 干净 server.state 装配（与既有队列测试同基座）。"""
    import server.state
    from session.store import SessionStore

    store = SessionStore(db_path=workspace / "sessions.db")
    store.add_workspace(str(workspace))
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "running_runs", {})
    from tools.subagent import notify

    notify._pending_notifications.clear()
    return store


def test_promote_inputs_tx_persists_and_flips_atomically(workspace, store_env):
    """b+) 目标 API：转正与消息写入一个事务——转正行落库且状态翻转同批生效。"""
    store = store_env
    sid = store.create_session(str(workspace)).id
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "排队补充"})

    promoted = store.peek_queued_inputs(sid)
    full = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}, *promoted]
    store.promote_inputs_tx(sid, full)

    rows = {r["id"] for r in store.list_queued_inputs(sid)}
    assert q1 not in rows  # 已脱离 queued
    stored = [m.get("content") for m in store.get_session(sid).messages]
    assert "排队补充" in stored, "转正消息必须随事务落库"
    row = store.get_promoted_input(q1)
    assert row is not None and row["status"] == "promoted"
    assert row["promoted_message_id"] == q1, "判据挂 _input_id（=队列行 id），非易失效的行主键"


def test_promote_inputs_tx_rolls_back_on_write_failure(workspace, store_env, monkeypatch):
    """b-) 目标 API：消息写入失败时整体回滚——队列行不得停在 promoted 而消息不在库。"""
    store = store_env
    sid = store.create_session(str(workspace)).id
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "会被吞的消息"})

    def _boom(*a, **kw):
        raise RuntimeError("落库失败")

    monkeypatch.setattr(store, "_replace_within", _boom)
    promoted = store.peek_queued_inputs(sid)
    with pytest.raises(RuntimeError):
        store.promote_inputs_tx(sid, [{"role": "user", "content": "新提问"}, *promoted])

    # 回滚后行仍可被转正（queued），不存在 promoted-无消息终态
    queued = {r["id"] for r in store.list_queued_inputs(sid)}
    assert q1 in queued


# ---------------------------------------------------------------------------
# b2) 唤醒消费翻转型：合并唤起恰注入一次
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_run_consumes_once_and_merges(workspace, store_env, monkeypatch):
    """唤醒路径：通知行经翻转型消费（merged_wake）合并为一条唤起 prompt，
    文本在最终历史恰出现一次（双消费回归钉）；行 id 由 _merged_input_ids
    承载，对账不误判丢失补写。"""
    import asyncio
    import json
    from types import SimpleNamespace

    import server.state
    from server.permission_bridge import PermissionBridge
    from server.question_bridge import QuestionBridge
    from server.routers.chat import routes as chat_routes
    from query.engine import QueryEngineConfig

    store = store_env
    sid = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid, [{"role": "user", "content": "旧问题"}])
    q = store.admit_session_input(
        sid, "backgroundNotification", "queue",
        {"role": "user", "content": "<task-notification>任务完成</task-notification>"},
    )

    engine_box: dict = {}

    class FakeEngine:
        def __init__(self):
            self.mutable_messages: list = []
            self.release = asyncio.Event()
            self.release.set()
            engine_box["engine"] = self

        async def submitMessage(self, prompt, user_context=None, system_context=None, message_meta=None):
            # 与真实引擎同形：message_meta（_merged_input_ids 等）并入 user 消息
            user_msg = {"role": "user", "content": prompt}
            if message_meta:
                user_msg.update(message_meta)
            self.mutable_messages.append(user_msg)
            await self.release.wait()
            yield {"role": "assistant", "content": "收到"}

    class FakeAppState:
        def get_state(self):
            return SimpleNamespace(
                token_usage=SimpleNamespace(
                    input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0, last_prompt_tokens=0,
                    last_cache_creation=0, total_input_tokens=0,
                ),
                model="test-model", total_cost_usd=0.0, context_breakdown=None,
            )

    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(server.state, "engine", FakeEngine())
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)
    from tools.subagent import notify

    notify._pending_notifications.clear()

    def fake_query_engine(config, initial_messages=None, session_id=""):
        e = FakeEngine()
        e.mutable_messages = list(initial_messages or [])
        return e

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())

    await asyncio.wait_for(chat_routes._wake_run(sid), timeout=5)
    await asyncio.sleep(0.05)  # 让后台引擎任务推进过首个挂起点

    stored = store.get_session(sid).messages
    contents = [m.get("content") for m in stored]
    assert contents.count("<task-notification>任务完成</task-notification>") == 1, (
        f"通知文本必须恰出现一次（双消费或丢失）: {contents}"
    )
    row = store.get_promoted_input(q)
    assert row["status"] == "promoted" and row["status_reason"] == "merged_wake"
    prompt_msg = [m for m in stored if m.get("_merged_input_ids")]
    assert prompt_msg and q in prompt_msg[0]["_merged_input_ids"], "合并承载判据缺失"
    # 对账：合并承载计入判据，不误判为丢失补写
    assert store.reconcile_promoted_inputs() == 0
    assert len(store.get_session(sid).messages) == len(stored)


@pytest.mark.asyncio
async def test_wake_paused_session_not_consumed(workspace, store_env, monkeypatch):
    """暂停门：auto_drain 关时会话不被唤醒消费——行留 queued、不起轮、
    历史无通知（转正点位共享一道门，后台通知不得强行唤起）。"""
    import asyncio
    from types import SimpleNamespace

    import server.state
    from server.permission_bridge import PermissionBridge
    from server.question_bridge import QuestionBridge
    from server.routers.chat import routes as chat_routes
    from query.engine import QueryEngineConfig

    store = store_env
    sid = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid, [{"role": "user", "content": "旧问题"}])
    q = store.admit_session_input(
        sid, "backgroundNotification", "queue",
        {"role": "user", "content": "<task-notification>暂停期间完成</task-notification>"},
    )
    # 用户点停止后的自动暂停态
    store.set_queue_auto_drain(sid, False, "stopped")

    class FakeEngine:
        def __init__(self):
            self.mutable_messages: list = []

        async def submitMessage(self, prompt, user_context=None, system_context=None, message_meta=None):
            self.mutable_messages.append({"role": "user", "content": prompt})
            yield {"role": "assistant", "content": "收到"}

    class FakeAppState:
        def get_state(self):
            return SimpleNamespace(
                token_usage=SimpleNamespace(
                    input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0, last_prompt_tokens=0,
                    last_cache_creation=0, total_input_tokens=0,
                ),
                model="test-model", total_cost_usd=0.0, context_breakdown=None,
            )

    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "running_runs", {})
    monkeypatch.setattr(server.state, "engine", FakeEngine())
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)
    from tools.subagent import notify

    notify._pending_notifications.clear()

    def fake_query_engine(config, initial_messages=None, session_id=""):
        return FakeEngine()

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())

    await asyncio.wait_for(chat_routes._wake_run(sid), timeout=5)
    await asyncio.sleep(0.05)

    row = store.get_promoted_input(q)
    assert row["status"] == "queued", "暂停态不得消费队列行"
    assert sid not in server.state.running_runs, "暂停会话不得被强行唤起起轮"
    contents = [m.get("content") for m in store.get_session(sid).messages]
    assert contents.count("<task-notification>暂停期间完成</task-notification>") == 0


def test_wake_crash_before_start_recovers_via_reconcile(workspace, store_env):
    """消费后、起轮落库前崩溃：启动对账按 payload 逐行补写，不丢失、幂等。"""
    store = store_env
    sid = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid, [{"role": "user", "content": "旧问题"}])
    q = store.admit_session_input(
        sid, "backgroundNotification", "queue",
        {"role": "user", "content": "<task-notification>崩溃窗口通知</task-notification>"},
    )
    # 消费翻转（模拟崩溃：之后起轮落库没有发生）
    msgs, ids = store.consume_for_wake(sid)
    assert ids == [q]
    assert len(store.get_session(sid).messages) == 1  # 消息尚未落库

    assert store.reconcile_promoted_inputs() == 1
    contents = [m.get("content") for m in store.get_session(sid).messages]
    assert contents.count("<task-notification>崩溃窗口通知</task-notification>") == 1
    row = store.get_promoted_input(q)
    assert row["promoted_message_id"] == q
    assert store.reconcile_promoted_inputs() == 0, "补写后幂等"


# ---------------------------------------------------------------------------
# c) 子会话持久化出口必须清洗
# ---------------------------------------------------------------------------


def test_child_session_persist_exit_leaves_no_dangling(workspace, store_env):
    """c) 在途悬空形态经持久化出口后不得原样落库：每个调用 id 必有结果行。"""
    from tools.subagent.session_binding import persist_child_messages

    store = store_env
    sid = "subagent_agent_hangtest"
    store.create_session(str(workspace), session_id=sid, origin="subagent")
    dangling = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_x", "type": "function", "function": {"name": "T", "arguments": "{}"}},
            {"id": "call_y", "type": "function", "function": {"name": "T", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_x", "content": "x 结果"},
    ]
    persist_child_messages(sid, dangling)

    stored = store.get_session(sid).messages
    ids = {m.get("tool_call_id") for m in stored if m.get("role") == "tool"}
    missing = {"call_x", "call_y"} - ids
    assert not missing, f"持久化出口未清洗，悬空调用落库: {missing}"
