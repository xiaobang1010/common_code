"""中断落盘 / Read 视觉 / 输入队列测试（interrupt-flush-image-read）。

R1：task.cancel 落在流式与工具等待段时的在途回合落盘与配对；
R2：Read 图片分支（视觉注入/拒绝/超限/大小写）、anthropic 合并、轮询脱敏；
R3：session_input 队列（受理/转正/撤销/notify 迁移/起轮先于新消息）。
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from query.config import build_query_config
from query.engine import QueryEngine, build_engine_config
from query.loop import query_loop
from query.services.api.llm import StreamEvent
from query.utils.messages import sanitize_dangling_tool_calls
from tools.protocol import CancellationPolicy, ToolResult, ToolUseContext, build_tool

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _EmptyInput(BaseModel):
    pass


# ---------------------------------------------------------------------------
# R1 取消落盘
# ---------------------------------------------------------------------------


def _hang_tool(message: str = "Hang 已取消"):
    """永挂工具：execute 等待一个永不置位的事件，取消文案可配。"""
    gate = asyncio.Event()

    async def _exec(inp, context):
        await gate.wait()
        return ToolResult(content="never")

    return build_tool(
        name="HangTool", description="挂起", input_schema=_EmptyInput,
        execute=_exec, prompt="", is_read_only=True,
        cancellation=CancellationPolicy(user_visible_message=message),
    )


def _fast_tool():
    async def _exec(inp, context):
        return ToolResult(content="fast-ok")

    return build_tool(
        name="FastTool", description="即时", input_schema=_EmptyInput,
        execute=_exec, prompt="", is_read_only=True,
    )


def _inject_tool():
    """完成即产出 new_messages 注入的工具（Skill/Agent 形态），验证置尾约束。"""
    async def _exec(inp, context):
        return ToolResult(
            content="inject-ok",
            new_messages=[{"role": "user", "content": "注入正文"}],
        )

    return build_tool(
        name="InjectTool", description="带注入", input_schema=_EmptyInput,
        execute=_exec, prompt="", is_read_only=True,
    )


@dataclass
class _ToolCallDeps:
    """一轮发起指定工具调用后滞留，供测试在工具等待/流式段取消。"""

    call_names: list[str]
    hold: float = 30.0
    calls: int = 0

    def get_uuid(self) -> str:
        return "u"

    async def call_model(self, messages=None, tools=None, **kw):
        self.calls += 1
        for idx, name in enumerate(self.call_names):
            yield StreamEvent(
                type="tool_call_delta", tool_call_index=idx,
                tool_call_id=f"call_{name}", tool_call_name=name,
            )
            yield StreamEvent(
                type="tool_call_delta", tool_call_index=idx, tool_call_arguments="{}",
            )
        yield StreamEvent(type="done", finish_reason="tool_calls")
        yield StreamEvent(type="usage", usage={"total_tokens": 5})
        await asyncio.sleep(self.hold)


@dataclass
class _SlowTextDeps:
    """流出半截文本后滞留（无工具调用），供流式段取消。"""

    hold: float = 30.0
    calls: int = 0

    def get_uuid(self) -> str:
        return "u"

    async def call_model(self, messages=None, tools=None, **kw):
        self.calls += 1
        yield StreamEvent(type="reasoning", content="思考半句")
        yield StreamEvent(type="content", content="前半段回答")
        await asyncio.sleep(self.hold)


@dataclass
class _PreOutputDeps:
    """首个事件前即滞留：取消时无任何产出可落。"""

    hold: float = 30.0

    def get_uuid(self) -> str:
        return "u"

    async def call_model(self, messages=None, tools=None, **kw):
        await asyncio.sleep(self.hold)
        yield StreamEvent(type="done", finish_reason="stop")


def _make_engine(tools, deps):
    config = build_engine_config(model="fake", tools=tools, max_turns=10, deps=deps)
    return QueryEngine(config, initial_messages=[{"role": "user", "content": "任务"}])


async def _run_and_cancel(engine, delay: float = 0.4):
    """驱动 query_loop 至滞留点后 cancel 整个消费任务。"""

    async def _drain():
        async for _ev in query_loop(engine, build_query_config(session_id="s_cancel")):
            pass

    task = asyncio.create_task(_drain())
    await asyncio.sleep(delay)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return engine


@pytest.mark.asyncio
async def test_cancel_during_pending_tool_flushes_paired_turn():
    """快+慢工具并行轮取消：在途 assistant 落库，真实结果保留、缺口补工具取消文案。"""
    engine = _make_engine([_fast_tool(), _hang_tool()], _ToolCallDeps(call_names=["FastTool", "HangTool"]))
    await _run_and_cancel(engine)
    msgs = engine.mutable_messages

    assistant = msgs[-3] if msgs[-1].get("role") == "tool" and len(msgs) >= 4 else None
    assert assistant is not None and assistant.get("role") == "assistant"
    assert len(assistant.get("tool_calls") or []) == 2
    tool_msgs = msgs[msgs.index(assistant) + 1:]
    by_id = {m.get("tool_call_id"): m.get("content") for m in tool_msgs if m.get("role") == "tool"}
    assert by_id.get("call_FastTool") == "fast-ok"
    assert by_id.get("call_HangTool") == "Hang 已取消"
    # 落盘序列对收尾清洗器幂等（不再需要它兜底补写）
    assert sanitize_dangling_tool_calls(list(msgs)) == msgs


@pytest.mark.asyncio
async def test_cancel_single_pending_tool_uses_tool_message():
    """单挂起工具取消（前台子代理场景同构）：合成正文取该工具取消文案。"""
    engine = _make_engine([_hang_tool("Agent 执行已取消")], _ToolCallDeps(call_names=["HangTool"]))
    await _run_and_cancel(engine)
    msgs = engine.mutable_messages
    assert msgs[-2].get("role") == "assistant" and len(msgs[-2]["tool_calls"]) == 1
    assert msgs[-1] == {**msgs[-1], "role": "tool", "tool_call_id": "call_HangTool", "content": "Agent 执行已取消"}
    assert sanitize_dangling_tool_calls(list(msgs)) == msgs


@pytest.mark.asyncio
async def test_cancel_during_stream_flushes_partial_text():
    """流式中途取消：半截文本与 reasoning 落成纯文本 assistant（无 tool_calls 无需配对）。"""
    engine = _make_engine([], _SlowTextDeps())
    await _run_and_cancel(engine)
    msgs = engine.mutable_messages
    assert msgs[-1].get("role") == "assistant"
    assert msgs[-1].get("content") == "前半段回答"
    assert msgs[-1].get("_reasoning") == "思考半句"
    assert "tool_calls" not in msgs[-1]


@pytest.mark.asyncio
async def test_cancel_flush_places_injected_messages_last():
    """带 new_messages 注入的工具场景：注入行必须置尾（不夹在工具结果之间），sanitize 幂等。"""
    engine = _make_engine(
        [_inject_tool(), _hang_tool()],
        _ToolCallDeps(call_names=["InjectTool", "HangTool"]),
    )
    await _run_and_cancel(engine)
    msgs = engine.mutable_messages
    assert msgs[-1].get("role") == "user" and msgs[-1].get("content") == "注入正文"
    assert msgs[-2].get("role") == "tool" and msgs[-2].get("content") == "Hang 已取消"
    assert msgs[-3].get("role") == "tool" and msgs[-3].get("content") == "inject-ok"
    assert msgs[-4].get("role") == "assistant"
    assert sanitize_dangling_tool_calls(list(msgs)) == msgs


@pytest.mark.asyncio
async def test_cancel_before_any_output_keeps_history():
    """无任何流出产出的取消：历史原样不动。"""
    engine = _make_engine([], _PreOutputDeps())
    await _run_and_cancel(engine)
    assert engine.mutable_messages == [{"role": "user", "content": "任务"}]


@pytest.mark.asyncio
async def test_cancel_tool_without_policy_falls_back_generic():
    """未声明取消政策的工具：合成正文回退默认策略文案（非通用中断常量）。"""
    async def _exec(inp, context):
        await asyncio.Event().wait()
        return ToolResult(content="never")

    plain = build_tool(
        name="PlainTool", description="无政策", input_schema=_EmptyInput,
        execute=_exec, prompt="", is_read_only=True,
    )
    engine = _make_engine([plain], _ToolCallDeps(call_names=["PlainTool"]))
    await _run_and_cancel(engine)
    assert engine.mutable_messages[-1].get("content") == "工具执行已取消"


# ---------------------------------------------------------------------------
# R2 Read 图片视觉
# ---------------------------------------------------------------------------


@pytest.fixture
def png_ws(workspace, monkeypatch):
    """工作区内放一张 1x1 PNG 与一个文本文件。"""
    (workspace / "shot.png").write_bytes(PNG_BYTES)
    (workspace / "note.txt").write_text("hello\nworld", encoding="utf-8")
    return workspace


def _read(path: str):
    from tools.implementations.file_read_tool.tool import _execute
    from tools.implementations.file_read_tool.schema import FileReadInput

    return asyncio.run(_execute(FileReadInput(file_path=path), ToolUseContext()))


def test_read_image_vision_injects_parts(png_ws, monkeypatch):
    monkeypatch.setenv("COMMON_CODE_MODEL", "gpt-4o")
    r = _read("shot.png")
    assert not r.is_error
    assert r.content == "[Attached image/png: Read image]"
    assert r.new_messages and r.new_messages[0]["role"] == "user"
    parts = r.new_messages[0]["content"]
    assert [b["type"] for b in parts] == ["text", "image_url"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # data_url 不进 metadata（轮询/SSE 不回传大 base64）
    assert "data_url" not in r.metadata
    # 上下文无二进制
    assert "\x00" not in r.content


def test_read_image_non_vision_rejected(png_ws, monkeypatch):
    monkeypatch.setenv("COMMON_CODE_MODEL", "some-text-model")
    r = _read("shot.png")
    assert r.is_error
    assert "不支持图片输入" in r.content
    assert not r.new_messages


def test_read_image_oversize_rejected(png_ws, monkeypatch):
    from tools.implementations.file_read_tool import handler

    monkeypatch.setenv("COMMON_CODE_MODEL", "gpt-4o")
    monkeypatch.setattr(handler, "_MAX_IMAGE_DECODED_BYTES", 10)
    r = _read("shot.png")
    assert r.is_error
    assert "上限" in r.content


def test_read_image_suffix_case_insensitive(png_ws, monkeypatch):
    (png_ws / "UP.PNG").write_bytes(PNG_BYTES)
    monkeypatch.setenv("COMMON_CODE_MODEL", "gpt-4o")
    r = _read("UP.PNG")
    assert r.content == "[Attached image/png: Read image]"


def test_read_text_regression(png_ws, monkeypatch):
    monkeypatch.setenv("COMMON_CODE_MODEL", "gpt-4o")
    r = _read("note.txt")
    assert not r.new_messages
    assert r.content.startswith("[文件基线]")
    assert "1→hello" in r.content


def test_anthropic_merges_injected_user_after_tool():
    from query.services.api.anthropic_llm import _to_anthropic_messages

    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "[Attached image/png: Read image]"},
        {"role": "user", "content": [
            {"type": "text", "text": "[Attached image/png: Read image]"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]},
    ]
    _, out = _to_anthropic_messages(msgs)
    assert [m["role"] for m in out] == ["assistant", "user"]
    blocks = out[1]["content"]
    assert [b["type"] for b in blocks] == ["tool_result", "text", "image"]


def test_anthropic_absorbs_plain_string_user_after_tool():
    from query.services.api.anthropic_llm import _to_anthropic_messages

    msgs = [
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
        {"role": "user", "content": "<task-notification>纯字符串</task-notification>"},
    ]
    _, out = _to_anthropic_messages(msgs)
    assert len(out) == 1 and out[0]["role"] == "user"
    assert [b["type"] for b in out[0]["content"]] == ["tool_result", "text"]


def test_anthropic_plain_consecutive_users_untouched():
    """与 tool 结果无关的连续 user 消息维持逐条透传。"""
    from query.services.api.anthropic_llm import _to_anthropic_messages

    msgs = [{"role": "user", "content": "一"}, {"role": "user", "content": "二"}]
    _, out = _to_anthropic_messages(msgs)
    assert [m["role"] for m in out] == ["user", "user"]


def test_omit_image_payloads_on_injected_message():
    from server.routers.chat.routes import _omit_image_payloads

    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "[Attached image/png: Read image]"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100}},
    ]}]
    out = _omit_image_payloads(msgs)
    dumped = json.dumps(out)
    assert "A" * 100 not in dumped
    assert "__omitted__" in dumped
    # 不修改引擎原消息
    assert "A" * 100 in json.dumps(msgs)


# ---------------------------------------------------------------------------
# R3 输入队列
# ---------------------------------------------------------------------------


@pytest.fixture
def store_env(workspace, monkeypatch):
    """真实 SessionStore + 干净 server.state 装配。"""
    import server.state
    from session.store import SessionStore

    store = SessionStore(db_path=workspace / "sessions.db")
    store.add_workspace(str(workspace))
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "running_runs", {})
    from tools.subagent import notify

    notify._pending_notifications.clear()
    return store


def test_store_admit_promote_order_and_ids(workspace, store_env):
    store = store_env
    sid = store.create_session(str(workspace)).id
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "排队消息"})
    q2 = store.admit_session_input(sid, "backgroundNotification", "queue", {"role": "user", "content": "通知"})
    assert store.count_queued_inputs(sid, "backgroundNotification") == 1
    assert len(store.list_queued_inputs(sid)) == 2

    promoted = store.promote_queued_inputs(sid)
    assert [m["content"] for m in promoted] == ["排队消息", "通知"]
    assert [m["_input_id"] for m in promoted] == [q1, q2]
    assert all("_ts" in m for m in promoted)
    assert store.promote_queued_inputs(sid) == []
    # 二次转正为空即幂等，不会重复注入
    assert store.count_queued_inputs(sid) == 0


def test_store_cancel_only_queued(workspace, store_env):
    store = store_env
    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "撤销我"})
    assert store.cancel_session_input(q) is True
    assert store.cancel_session_input(q) is False
    assert store.list_queued_inputs(sid) == []
    assert store.promote_queued_inputs(sid) == []


def test_list_queued_inputs_omits_image_base64(workspace, store_env):
    store = store_env
    sid = store.create_session(str(workspace)).id
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "B" * 80}},
    ]})
    items = store.list_queued_inputs(sid)
    assert items[0]["content"][1]["image_url"]["url"] == "__omitted__"
    assert items[0]["content"][0]["text"] == "看图"


@pytest.mark.asyncio
async def test_chat_running_enqueues(workspace, store_env):
    """运行中发送：受理入队返回 JSON（不再拒绝），行以 queued 落表。"""
    import server.state
    from server.routers.chat.routes import chat
    from starlette.responses import JSONResponse

    store = store_env
    sid = store.create_session(str(workspace)).id
    server.state.running_runs[sid] = object()

    resp = await chat({"prompt": "运行中消息", "session_id": sid})
    assert isinstance(resp, JSONResponse)
    body = json.loads(resp.body)
    assert body["queued"] is True and body["queue_id"].startswith("queue_")
    items = store.list_queued_inputs(sid)
    assert len(items) == 1 and items[0]["kind"] == "sendText"


@pytest.mark.asyncio
async def test_chat_idle_starts_stream(workspace, store_env):
    """空闲发送：仍走 SSE 流路径（startNow 回归不变）。"""
    from server.routers.chat.routes import chat
    from starlette.responses import StreamingResponse

    store = store_env
    sid = store.create_session(str(workspace)).id
    resp = await chat({"prompt": "空闲消息", "session_id": sid})
    assert isinstance(resp, StreamingResponse)


@pytest.mark.asyncio
async def test_start_run_promotes_queued_before_new_message(workspace, monkeypatch):
    """起轮转正：旧 queued 行进入前缀、排在新 prompt 之前（防插队）。"""
    import server.state
    from server.routers.chat import routes as chat_routes
    from server.permission_bridge import PermissionBridge
    from server.question_bridge import QuestionBridge
    from session.store import SessionStore
    from tools.subagent import notify

    store = SessionStore(db_path=workspace / "sessions.db")
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    store.save_messages(sid, [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}])
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "排队的补充"})

    engine_box: dict = {}

    class FakeEngine:
        def __init__(self):
            self.mutable_messages: list = []
            self.release = asyncio.Event()
            self.release.set()
            engine_box["engine"] = self

        async def submitMessage(self, prompt, user_context=None, system_context=None):
            self.mutable_messages.append({"role": "user", "content": prompt})
            await self.release.wait()
            yield {"role": "assistant", "content": "回复"}

    class FakeAppState:
        def get_state(self):
            return SimpleNamespace(
                token_usage=SimpleNamespace(
                    input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0, last_prompt_tokens=0,
                    last_cache_creation=0, total_input_tokens=0,
                ),
                model="test-model", total_cost_usd=0.0,
            )

    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "running_runs", {})
    # 视图回写路径会触碰 server.state.engine，装配一个占位实例
    monkeypatch.setattr(server.state, "engine", FakeEngine())
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)

    def fake_query_engine(config, initial_messages=None, session_id=""):
        e = FakeEngine()
        e.mutable_messages = list(initial_messages or [])
        return e

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    from query.engine import QueryEngineConfig

    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    notify._pending_notifications.clear()

    async def _collect():
        async for _chunk in chat_routes.chat_event_stream("新消息", sid):
            pass

    await asyncio.wait_for(_collect(), timeout=5)
    engine = engine_box["engine"]
    contents = [m.get("content") for m in engine.mutable_messages]
    assert contents.index("排队的补充") < contents.index("新消息")
    assert store.count_queued_inputs(sid) == 0


def test_notify_push_drain_pending_via_table(workspace, store_env):
    """notify 迁移：push 写表、pending_count 只统计通知、drain 转正取回。"""
    import server.state
    from tools.subagent import notify

    store = store_env
    sid = store.create_session(str(workspace)).id
    store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "用户行"})

    notify.push_notification(sid, {"role": "user", "content": "<task-notification>x</task-notification>"})
    assert notify.pending_count(sid) == 1  # 仅 backgroundNotification
    got = notify.drain_notifications(sid)
    assert [m["content"] for m in got] == ["用户行", "<task-notification>x</task-notification>"]
    assert notify.pending_count(sid) == 0


def test_notify_wakeup_hook_fires_on_table_push(workspace, store_env):
    """入表路径同样触发唤起钩子（父空闲 auto-resume 管线不变）。"""
    from tools.subagent import notify

    store = store_env
    sid = store.create_session(str(workspace)).id
    woken: list[str] = []
    notify.register_wakeup_hook(lambda s: woken.append(s))
    try:
        notify.push_notification(sid, {"role": "user", "content": "n"})
    finally:
        notify.register_wakeup_hook(None)  # type: ignore[arg-type]
    assert woken == [sid]


def test_notify_memory_fallback_without_store(workspace, monkeypatch):
    """无 store 环境（单测/无 server）：回退内存队列，行为与旧实现一致。"""
    import server.state
    from tools.subagent import notify

    monkeypatch.setattr(server.state, "session_store", None)
    notify._pending_notifications.clear()
    notify.push_notification("sess_x", {"role": "user", "content": "内存通知"})
    assert notify.pending_count("sess_x") == 1
    assert [m["content"] for m in notify.drain_notifications("sess_x")] == ["内存通知"]
    assert notify.pending_count("sess_x") == 0
