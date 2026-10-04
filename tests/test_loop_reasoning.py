"""思维链 _reasoning/_reasoning_ms 落库字段单测。

验证：有思考输出的回合 assistant 消息写入两个下划线内部字段、无思考回合不含；
字段随会话整表 JSON 落库可回读；发给模型前被 _build_messages 剥离不泄漏。
"""

from __future__ import annotations

from query.loop import _build_assistant_message
from query.services.api.llm import _build_messages
from session.store import SessionStore


def test_build_assistant_message_with_reasoning():
    msg = _build_assistant_message(
        ["正文"],
        [{"id": "call_1", "function": {"name": "bash", "arguments": "{}"}}],
        ["先想想", "再动手"],
        1000.0,
        3500.0,
    )
    assert msg["_reasoning"] == "先想想再动手"
    assert msg["_reasoning_ms"] == 2500
    # 既有字段不受影响
    assert msg["content"] == "正文"
    assert msg["tool_calls"]


def test_build_assistant_message_without_reasoning():
    msg = _build_assistant_message(["hi"], [])
    assert "_reasoning" not in msg
    assert "_reasoning_ms" not in msg


def test_build_assistant_message_partial_reasoning_args():
    # 有文本但缺时间戳（异常形态）：不写字段，不抛错
    msg = _build_assistant_message(["hi"], [], ["想想"], None, None)
    assert "_reasoning" not in msg


def test_reasoning_stripped_before_model():
    msgs = [
        {
            "role": "assistant",
            "content": "hi",
            "_ts": 1.0,
            "_reasoning": "内部思考",
            "_reasoning_ms": 100,
        },
    ]
    out = _build_messages(msgs)
    assert out == [{"role": "assistant", "content": "hi"}]


def test_reasoning_persisted_roundtrip(tmp_path):
    # 整表 JSON 落库：新字段无需 schema 变更即可回读
    store = SessionStore(db_path=tmp_path / "sessions.db")
    session = store.create_session(workspace_path=str(tmp_path))
    messages = [
        {"role": "user", "content": "问题", "_ts": 1.0},
        _build_assistant_message(
            ["回答"], [], ["思考过程全文"], 1000.0, 2000.0
        ),
    ]
    assert store.save_messages(session.id, messages)
    loaded = store.get_session(session.id)
    assert loaded is not None
    assistant = loaded.messages[1]
    assert assistant["_reasoning"] == "思考过程全文"
    assert assistant["_reasoning_ms"] == 1000
