"""会话绑定测试：自动建会话、立即持久化、收尾时序、切换/删除安全。

直接驱动 chat_event_stream 生成器（不经过 HTTP），用 FakeEngine 模拟引擎，
验证：空 session_id 自动建会话并回传 session_meta、未选工作区拒绝、
立即持久化为"快照+新消息"、标题即时生成、finally 先保存后清理、
防御检查跳过错存、switch/delete 统一中止+等待、超时不硬切。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

import server.state
from server.routers.chat.routes import chat_event_stream
from server.routers.sessions.routes import delete_session, switch_session
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from session.store import SessionStore


class FakeEngine:
    """假引擎：mutable_messages + submitMessage 异步生成器。

    started 事件标记引擎已进入（用户消息已追加），release 放行产出回复。
    """

    def __init__(self, messages: list | None = None) -> None:
        self.mutable_messages: list = messages or []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def submitMessage(self, prompt, user_context=None, system_context=None):
        self.mutable_messages.append({"role": "user", "content": prompt})
        self.started.set()
        await self.release.wait()
        self.mutable_messages.append({"role": "assistant", "content": "回复内容"})
        yield {"role": "assistant", "content": "回复内容"}


class FakeAppState:
    """假 AppState：token 累加用。"""

    def get_state(self):
        return SimpleNamespace(
            token_usage=SimpleNamespace(
                input_tokens=0,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                last_prompt_tokens=0,
                last_cache_creation=0,
            ),
            model="test-model",
            total_cost_usd=0.0,
        )


@pytest.fixture
def env(workspace, monkeypatch):
    """装配 server.state：FakeEngine + 真实 SessionStore + 真实桥。

    新模型下 chat_event_stream 用 RunContext 创建任务引擎（QueryEngine），
    测试把 chat 路由里的 QueryEngine 替换为共享的 FakeEngine：
    任务引擎与查看视图引擎是同一个对象，测试可观测其消息与 started/release。
    """
    from server.routers import chat as chat_module
    from server.routers.chat import routes as chat_routes

    store = SessionStore(db_path=workspace / "sessions.db")
    engine = FakeEngine()
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})
    def fake_query_engine(config, initial_messages=None, session_id=""):
        # 模拟 QueryEngine(config, initial_messages)：快照装入任务引擎
        engine.mutable_messages = list(initial_messages or [])
        return engine

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    # build_engine_config 在测试环境可能读真实配置，替换为最小配置
    from query.engine import QueryEngineConfig
    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    # 注册表清空用同一份引用，收尾 pop 不残留
    server.state.running_runs.clear()
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "current_task", None)
    monkeypatch.setattr(server.state, "current_session_id", None)
    monkeypatch.setattr(server.state, "engine_session_id", None)
    # 超时调小，超时分支测试不用等 10 秒。
    # 收尾事件按事件循环惰性创建：每个测试是新 loop -> 新 Event，无需手动 clear
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)
    return engine, store


async def collect(gen):
    """消费 SSE 生成器，返回解析后的事件列表。"""
    events = []
    async for chunk in gen:
        line = chunk.split("data: ", 1)[1].strip()
        events.append(json.loads(line))
    return events


# --- 自动建会话 ---


@pytest.mark.asyncio
async def test_auto_create_session_and_session_meta(workspace, env):
    engine, store = env
    store.add_workspace(str(workspace))

    engine.release.set()  # 放行引擎产出回复
    events = await collect(chat_event_stream("你好世界", ""))

    # 首个事件是 session_meta
    assert events[0]["type"] == "session_meta"
    sid = events[0]["session_id"]
    assert sid
    session = store.get_session(sid)
    assert session is not None
    # 标题即时生成
    assert session.title == "你好世界"
    # 引擎消息列表已被重置（空快照），不会残留其他会话历史
    assert engine.mutable_messages == [
        {"role": "user", "content": "你好世界"},
        {"role": "assistant", "content": "回复内容"},
    ]


@pytest.mark.asyncio
async def test_existing_session_not_recreated(workspace, env):
    """已有 session_id 的对话不重复建会话，session_meta 回传原 id。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    engine.release.set()

    events = await collect(chat_event_stream("消息", sid))

    assert events[0]["type"] == "session_meta"
    assert events[0]["session_id"] == sid
    assert len(store.list_sessions(str(workspace))) == 1  # 没有新建


@pytest.mark.asyncio
async def test_reject_when_workspace_not_registered(workspace, env):
    engine, store = env
    # 工作区未登记（未选工作区）
    events = await collect(chat_event_stream("你好", ""))

    assert events[0]["type"] == "error"
    assert store.list_sessions(str(workspace)) == []
    # 引擎列表未被重置（拒绝发生在重置之前）
    assert engine.mutable_messages == []


@pytest.mark.asyncio
async def test_engine_reset_prevents_history_leak(workspace, env):
    """删除当前会话后引擎残留历史：自动建会话前先重置，历史不串入新会话。"""
    engine, store = env
    store.add_workspace(str(workspace))
    # 引擎残留上一个会话的历史（界面已显示无会话）
    engine.mutable_messages = [{"role": "user", "content": "别人的历史"}]

    engine.release.set()
    events = await collect(chat_event_stream("新对话", ""))

    assert events[0]["type"] == "session_meta"
    sid = events[0]["session_id"]
    session = store.get_session(sid)
    # 新会话只有本次对话（含兜底保存的回复），不含残留历史
    assert session.messages == [
        {"role": "user", "content": "新对话"},
        {"role": "assistant", "content": "回复内容"},
    ]


# --- 立即持久化与标题 ---


@pytest.mark.asyncio
async def test_immediate_persist_snapshot_plus_user(workspace, env):
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    # 前缀在 DB 会话消息里（新模型快照来源），历史消息先落库
    store.save_messages(sid, [{"role": "user", "content": "旧历史"}])

    consumer = asyncio.create_task(collect(chat_event_stream("新消息", sid)))
    # 引擎进入后、回复产出前：DB 已保存"快照 + 本条用户消息"
    await engine.started.wait()
    session = store.get_session(sid)
    # 立即持久化的「新消息」带 _ts（routes 打标），按 role/content 断言忽略 _ts
    assert [(m["role"], m["content"]) for m in session.messages] == [
        ("user", "旧历史"),
        ("user", "新消息"),
    ]
    # 正向断言：快照 user 已打 _ts（旧历史前缀来自 DB，不打标）
    assert isinstance(session.messages[1]["_ts"], (int, float))
    assert session.messages[1]["_ts"] > 0

    engine.release.set()
    await consumer
    # 收尾兜底保存完整列表
    session = store.get_session(sid)
    assert session.messages[-1] == {"role": "assistant", "content": "回复内容"}


@pytest.mark.asyncio
async def test_title_immediate_for_existing_session(workspace, env):
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace), title="").id

    engine.release.set()
    await collect(chat_event_stream("首条消息就生成标题", sid))

    session = store.get_session(sid)
    assert session.title == "首条消息就生成标题"


# --- 收尾时序与防御检查 ---


@pytest.mark.asyncio
async def test_finalize_order_save_then_cleanup(workspace, env):
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    engine.release.set()
    await collect(chat_event_stream("消息", sid))

    # 消息已保存（含 assistant），任务已移出注册表，收尾事件已置位，
    # 查看视图被回写（engine_session_id 未变 -> 全局视图同步）
    session = store.get_session(sid)
    assert any(m.get("role") == "assistant" for m in session.messages)
    assert sid not in server.state.running_runs
    assert server.state.running_runs.get(sid) is None
    assert server.state.engine_session_id == sid
    assert engine.mutable_messages[-1] == {"role": "assistant", "content": "回复内容"}


@pytest.mark.asyncio
async def test_switch_view_no_view_writeback_but_task_saves(workspace, env):
    """run 期间切走查看会话：不回写全局视图，但任务自包含保存完整内容。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id

    consumer = asyncio.create_task(collect(chat_event_stream("消息", sid)))
    await engine.started.wait()
    # 切走查看会话（模拟 switch 到别处）：engine_session_id 被改变
    server.state.engine_session_id = "other-session"
    engine.release.set()
    await consumer

    # 任务引擎独立 -> 收尾保存完整内容（含 assistant）到绑定会话
    session = store.get_session(sid)
    assert any(m.get("role") == "assistant" for m in session.messages)
    # 查看视图未回写（engine_session_id 已被切走）
    assert server.state.engine_session_id == "other-session"


@pytest.mark.asyncio
async def test_bridge_pending_cleared_by_source_on_finalize(workspace, env):
    """任务收尾只清本会话的请求，不误删其他并发任务的挂起请求。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    pb = server.state.permission_bridge
    # 本会话的悬挂请求 + 其他会话的悬挂请求
    pb._pending_meta["req-a"] = {"type": "permission_request", "request_id": "req-a", "session_id": sid}
    pb._pending_meta["req-b"] = {"type": "permission_request", "request_id": "req-b", "session_id": "other"}

    engine.release.set()
    await collect(chat_event_stream("消息", sid))

    # 本任务的请求被清，其他会话的保留
    ids = [r["request_id"] for r in pb.get_pending_requests()]
    assert "req-a" not in ids
    assert "req-b" in ids


# --- 切换/删除安全 ---


def _register_fake_run(session_id, engine, finalize_on_cancel: bool = True):
    """构造并注册一个 RunContext：任务被 cancel 时可选置位 finished。"""
    run = server.state.RunContext(session_id=session_id, engine=engine)

    async def fake_task():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        finally:
            if finalize_on_cancel:
                # 模拟真实任务的收尾：置位 finished + 移出注册表
                run.finished.set()
                server.state.running_runs.pop(session_id, None)

    run.task = asyncio.create_task(fake_task())
    server.state.running_runs[session_id] = run
    return run


@pytest.mark.asyncio
async def test_switch_does_not_interrupt_running_task(workspace, env):
    """切换会话不中止运行中的任务：任务继续在注册表中，视图换成目标会话。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid_a = store.create_session(str(workspace)).id
    sid_b = store.create_session(str(workspace)).id
    # A 有运行任务（后台继续跑）
    run = _register_fake_run(sid_a, engine)
    await asyncio.sleep(0)
    server.state.engine_session_id = sid_a

    result = await switch_session(sid_b)

    assert result["ok"] is True
    # 视图引擎换成目标会话的消息
    assert engine.mutable_messages == store.get_session(sid_b).messages
    assert server.state.engine_session_id == sid_b
    # 任务未被中止：仍在注册表中、未 done
    assert server.state.running_runs.get(sid_a) is run
    assert not run.task.done()
    # 清理：收尾任务避免残留
    run.task.cancel()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_delete_timeout_no_hard_delete(workspace, env):
    """等待收尾超时：不硬删，会话保留，返回错误。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    # 任务收尾永不完成（cancel 不置位 finished）
    _register_fake_run(sid, engine, finalize_on_cancel=False)
    await asyncio.sleep(0)
    server.state.stream_finalize_timeout = 0.2

    result = await delete_session(sid)

    # 超时不硬删：409 错误，会话与注册表保留
    assert isinstance(result, JSONResponse) and result.status_code == 409
    assert store.get_session(sid) is not None
    assert server.state.running_runs.get(sid) is not None


@pytest.mark.asyncio
async def test_delete_running_session_waits(workspace, env):
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    run = _register_fake_run(sid, engine)
    await asyncio.sleep(0)  # 让任务被调度进入运行态

    result = await delete_session(sid)

    assert result["ok"] is True
    assert store.get_session(sid) is None
    assert run.finished.is_set()  # 任务被中止并完成收尾
    assert sid not in server.state.running_runs


@pytest.mark.asyncio
async def test_delete_non_running_session_direct(workspace, env):
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    server.state.current_session_id = None

    result = await delete_session(sid)
    assert result["ok"] is True
    assert store.get_session(sid) is None


# --- 删除工作区 / 删除会话引擎重置 / 收尾等待加固 ---


@pytest.mark.asyncio
async def test_delete_workspace_stops_running_task(workspace, env):
    """删除工作区：运行任务属于该工作区时先中止 + 等待收尾再删。"""
    from server.routers.workspaces.routes import delete_workspace

    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    run = _register_fake_run(sid, engine)
    await asyncio.sleep(0)  # 让任务被调度进入运行态

    result = await delete_workspace({"path": str(workspace)})

    assert result["ok"] is True
    assert run.finished.is_set()  # 任务被中止并完成收尾
    # 工作区及名下会话已删
    assert store.get_session(sid) is None
    assert store.list_workspaces() == []


@pytest.mark.asyncio
async def test_delete_current_session_resets_engine(workspace, env):
    """删除引擎当前装载的会话：引擎消息列表被重置，残留历史不串入后续。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    server.state.engine_session_id = sid
    engine.mutable_messages = [{"role": "user", "content": "A 的历史"}]

    result = await delete_session(sid)

    assert result["ok"] is True
    assert engine.mutable_messages == []
    assert server.state.engine_session_id is None


@pytest.mark.asyncio
async def test_delete_other_session_keeps_engine(workspace, env):
    """删除非引擎装载的会话：引擎列表不受影响。"""
    engine, store = env
    store.add_workspace(str(workspace))
    sid_a = store.create_session(str(workspace)).id
    sid_b = store.create_session(str(workspace)).id
    server.state.engine_session_id = sid_a
    engine.mutable_messages = [{"role": "user", "content": "A 的历史"}]

    result = await delete_session(sid_b)

    assert result["ok"] is True
    assert engine.mutable_messages == [{"role": "user", "content": "A 的历史"}]
    assert server.state.engine_session_id == sid_a


@pytest.mark.asyncio
async def test_stop_session_run_waits_finished_event(workspace, env):
    """stop_session_run：任务已结束但 finished 未置位时仍等待（不靠 task done 放行）。"""
    from server.routers.sessions.routes import stop_session_run

    engine, store = env
    sid = store.create_session(str(workspace)).id
    # 任务立即完成但不置位 finished（finished 由任务收尾 finally 置位）
    run = server.state.RunContext(session_id=sid, engine=engine)

    async def finished_task():
        pass

    run.task = asyncio.create_task(finished_task())
    server.state.running_runs[sid] = run
    await asyncio.sleep(0)  # 任务已完成
    server.state.stream_finalize_timeout = 0.2

    assert await stop_session_run(sid) is False  # 等待超时（事件未置位）
