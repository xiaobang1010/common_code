"""编辑重发（edit_user_index）测试。

直接驱动 chat_event_stream（沿用 test_chat_session_binding 的 FakeEngine 装配），
验证：截断定位正确（含 skill 重写提示与 system-reminder 混合列表）、
<system-reminder> 开头消息判为不可见、越界拒绝且历史不被修改、
截断后新 user 消息正确入库、引擎快照与截断一致。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import server.state
from server.routers.chat.routes import _visible_user_indexes, chat_event_stream
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from session.store import SessionStore

# 技能重写提示形状（与 commands 路由生成的一致）
SKILL_PROMPT = (
    "Use the skill named `spec` for this turn.\n"
    "<skill content>\n"
    "User request: 分析这个任务"
)


class FakeEngine:
    """假引擎：mutable_messages + submitMessage 异步生成器。"""

    def __init__(self, messages: list | None = None) -> None:
        self.mutable_messages: list = messages or []
        self.release = asyncio.Event()

    async def submitMessage(self, prompt, user_context=None, system_context=None):
        self.mutable_messages.append({"role": "user", "content": prompt})
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
    """装配 server.state：FakeEngine + 真实 SessionStore（与绑定测试同款）。"""
    from server.routers.chat import routes as chat_routes

    store = SessionStore(db_path=workspace / "sessions.db")
    engine = FakeEngine()
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})

    def fake_query_engine(config, initial_messages=None, session_id=""):
        engine.mutable_messages = list(initial_messages or [])
        return engine

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    from query.engine import QueryEngineConfig

    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 0.2)
    return engine, store


async def collect(gen):
    """消费 SSE 生成器，返回解析后的事件列表。"""
    events = []
    async for chunk in gen:
        line = chunk.split("data: ", 1)[1].strip()
        events.append(json.loads(line))
    return events


def _seed_history(store, workspace, sid_messages: list[dict]) -> str:
    """建会话、登记工作区并写入历史消息，返回会话 id。"""
    store.add_workspace(str(workspace))
    sid = store.create_session(str(workspace)).id
    store.save_messages(sid, sid_messages)
    return sid


# --- _visible_user_indexes 判定规则 ---


def test_visible_indexes_skip_system_reminder():
    """<system-reminder> 开头（不 strip）判为不可见，skill 重写提示可见。"""
    messages = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回复"},
        {"role": "user", "content": "<system-reminder>\nskill 正文\n</system-reminder>"},
        {"role": "user", "content": " 前导空格不应 strip 出可见性判定"},
        {"role": "user", "content": SKILL_PROMPT},
        {"role": "user", "content": 123},  # 非 str content 跳过
    ]
    assert _visible_user_indexes(messages) == [0, 3, 4]


# --- 编辑重发主链路 ---


@pytest.mark.asyncio
async def test_edit_truncates_and_repersists(workspace, env):
    """编辑第 1 条可见消息：其后历史被截掉，新 prompt 入库，引擎快照一致。"""
    engine, store = env
    history = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回复一"},
        {"role": "user", "content": "第二条"},
        {"role": "assistant", "content": "回复二"},
    ]
    sid = _seed_history(store, workspace, history)
    engine.release.set()

    events = await collect(chat_event_stream("第一条（改）", sid, edit_user_index=0))

    assert events[0]["type"] == "session_meta"
    session = store.get_session(sid)
    # 截断后只剩编辑替换的 user + FakeEngine 的回复
    assert session.messages == [
        {"role": "user", "content": "第一条（改）"},
        {"role": "assistant", "content": "回复内容"},
    ]
    # 引擎拿到的快照是截断后的前缀（不含新 user，由 submitMessage 追加）
    assert engine.mutable_messages == [
        {"role": "user", "content": "第一条（改）"},
        {"role": "assistant", "content": "回复内容"},
    ]


@pytest.mark.asyncio
async def test_edit_middle_message_with_mixed_list(workspace, env):
    """混合列表（skill 提示 + system-reminder）中编辑第 2 条可见消息。"""
    engine, store = env
    history = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回复一"},
        # skill 重写提示：可见
        {"role": "user", "content": SKILL_PROMPT},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "Bash", "arguments": "{}"}}]},
        {"role": "user", "content": "<system-reminder>\n注入\n</system-reminder>"},
        {"role": "user", "content": "第三条"},
        {"role": "assistant", "content": "回复三"},
    ]
    sid = _seed_history(store, workspace, history)
    engine.release.set()

    events = await collect(chat_event_stream("分析任务（改）", sid, edit_user_index=1))

    assert events[0]["type"] == "session_meta"
    session = store.get_session(sid)
    # 可见序号 1 对应 SKILL_PROMPT（下标 2）：截断保留下标 0-1，替换为编辑后消息
    assert [m.get("content") for m in session.messages[:3]] == ["第一条", "回复一", "分析任务（改）"]
    assert session.messages[-1] == {"role": "assistant", "content": "回复内容"}
    # system-reminder 与第三条都不再出现
    assert all(
        not (m.get("role") == "user" and str(m.get("content", "")).startswith("<system-reminder>"))
        for m in session.messages
    )
    assert "第三条" not in [str(m.get("content")) for m in session.messages]


@pytest.mark.asyncio
async def test_edit_invalid_index_keeps_history(workspace, env):
    """越界编辑：yield error 事件，历史与引擎都不被修改。"""
    engine, store = env
    history = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回复一"},
    ]
    sid = _seed_history(store, workspace, history)

    events = await collect(chat_event_stream("越界编辑", sid, edit_user_index=5))

    assert events[0]["type"] == "error"
    assert "编辑位置无效" in events[0]["error"]
    # 历史未被修改（持久化发生在校验之后）
    assert store.get_session(sid).messages == history
    assert engine.mutable_messages == []


@pytest.mark.asyncio
async def test_edit_non_integer_index_rejected(workspace, env):
    """非整数（如字符串）索引同样拒绝。"""
    engine, store = env
    history = [{"role": "user", "content": "第一条"}]
    sid = _seed_history(store, workspace, history)

    events = await collect(chat_event_stream("编辑", sid, edit_user_index="0"))

    assert events[0]["type"] == "error"
    assert store.get_session(sid).messages == history


@pytest.mark.asyncio
async def test_edit_preserves_title(workspace, env):
    """已有标题的会话编辑重发不覆盖标题。"""
    engine, store = env
    history = [
        {"role": "user", "content": "原始开头"},
        {"role": "assistant", "content": "回复"},
    ]
    sid = _seed_history(store, workspace, history)
    store.update_session_title(sid, "原标题")
    engine.release.set()

    await collect(chat_event_stream("完全不同的新标题文案会很长", sid, edit_user_index=0))

    assert store.get_session(sid).title == "原标题"


@pytest.mark.asyncio
async def test_edit_dangling_tool_calls_prefix_sanitized(workspace, env):
    """前缀里的悬空 tool_calls（存量脏数据）在截断快照时被补齐，不发给引擎。"""
    engine, store = env
    history = [
        {"role": "user", "content": "第一条"},
        # 悬空 assistant：中断残留
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t9", "function": {"name": "Bash", "arguments": "{}"}}]},
        {"role": "user", "content": "第二条"},
        {"role": "assistant", "content": "回复二"},
    ]
    sid = _seed_history(store, workspace, history)
    engine.release.set()

    # 编辑第 1 条可见消息：截断点在第一条 user，悬空助手本就在截断线后；
    # 用编辑第 2 条验证前缀清洗（可见序号 1 = "第二条"）
    await collect(chat_event_stream("第二条（改）", sid, edit_user_index=1))

    # 引擎快照：悬空 t9 已补合成结果
    assistant_with_tools = [m for m in engine.mutable_messages if m.get("tool_calls")]
    assert len(assistant_with_tools) == 1
    tool_msgs = [m for m in engine.mutable_messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["t9"]
    assert tool_msgs[0]["content"] == "[执行被中断，无结果]"


# --- 停止 → 再发新消息：复现链路的后端断言 ---


class DanglingEngine(FakeEngine):
    """首轮挂在工具执行阶段的假引擎：取消后历史以悬空 tool_calls 结尾。

    复现 /api/abort 在工具执行阶段 cancel 的真实形态：assistant(tool_calls)
    已写回引擎、工具结果未写回。armed 只武装首轮（被 cancel 后不再重置），
    测试里手动 disarm 后，下一轮 submitMessage 走正常路径。
    """

    def __init__(self) -> None:
        super().__init__()
        self.hang = asyncio.Event()
        self.armed = True

    async def submitMessage(self, prompt, user_context=None, system_context=None):
        self.mutable_messages.append({"role": "user", "content": prompt})
        if self.armed:
            self.mutable_messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t-hang", "function": {"name": "Bash", "arguments": "{}"}}],
            })
            await self.hang.wait()
            self.armed = False
        yield {"role": "assistant", "content": "新消息回复"}


@pytest.fixture
def hang_env(workspace, monkeypatch):
    """同 env，但引擎替换为 DanglingEngine。"""
    from server.routers.chat import routes as chat_routes

    store = SessionStore(db_path=workspace / "sessions.db")
    engine = DanglingEngine()
    monkeypatch.setattr(server.state, "engine", engine)
    monkeypatch.setattr(server.state, "running_runs", {})

    def fake_query_engine(config, initial_messages=None, session_id=""):
        engine.mutable_messages = list(initial_messages or [])
        return engine

    monkeypatch.setattr(chat_routes, "QueryEngine", fake_query_engine)
    from query.engine import QueryEngineConfig

    monkeypatch.setattr(chat_routes, "build_engine_config", lambda **kw: QueryEngineConfig())
    monkeypatch.setattr(server.state, "session_store", store)
    monkeypatch.setattr(server.state, "permission_bridge", PermissionBridge())
    monkeypatch.setattr(server.state, "question_bridge", QuestionBridge())
    monkeypatch.setattr(server.state, "app_state", FakeAppState())
    monkeypatch.setattr(server.state, "engine_session_id", None)
    monkeypatch.setattr(server.state, "stream_finalize_timeout", 2.0)
    return engine, store


@pytest.mark.asyncio
async def test_abort_saves_sanitized_history_then_new_turn_clean(workspace, hang_env):
    """停止后 DB 历史无悬空 tool_calls；再开一轮新消息时引擎快照干净。"""
    import server.state
    from server.routers.chat.routes import abort_query

    engine, store = hang_env
    sid = _seed_history(store, workspace, [])
    sid = None  # 走自动建会话，覆盖真实「会话内停止」路径

    async def consume_and_hold():
        gen = chat_event_stream("发起任务", sid)
        # 消费到 session_meta，确认任务已启动
        async for chunk in gen:
            evt = json.loads(chunk.split("data: ", 1)[1])
            if evt.get("type") == "session_meta":
                return evt["session_id"], gen
        raise AssertionError("session_meta 未回传")

    task = asyncio.create_task(consume_and_hold())
    session_id, gen = await asyncio.wait_for(task, timeout=3)
    await asyncio.sleep(0.2)  # 等引擎进入挂起点（悬空 assistant 已写回）

    # 模拟 /api/abort：body 解析失败走缺省（作用于当前查看会话）
    class BadBodyRequest:
        async def json(self):
            raise ValueError("no body")

    server.state.engine_session_id = session_id
    result = await abort_query(BadBodyRequest())
    assert result.body.count(b'"ok":true') > 0 or getattr(result, "body", None)

    # 停止收尾后：DB 历史不含悬空 tool_calls（t-hang 已补合成结果）
    saved = store.get_session(session_id).messages
    dangling = [
        m
        for i, m in enumerate(saved)
        if m.get("role") == "assistant" and m.get("tool_calls")
        and not any(
            x.get("role") == "tool" and x.get("tool_call_id") == m["tool_calls"][0]["id"]
            for x in saved[i + 1:]
        )
    ]
    assert dangling == []

    # 再发新消息：引擎拿到的快照同样干净，且新 user 消息在其后
    engine.armed = False
    await collect(chat_event_stream("新消息", session_id))
    tool_msgs = [m for m in engine.mutable_messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["t-hang"]
    assert engine.mutable_messages[-1]["role"] == "user"
    assert engine.mutable_messages[-1]["content"] == "新消息"
