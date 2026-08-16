"""消息时间戳 _ts 打标与剥离单测。

验证：assistant/tool 消息构造时打 _ts、_build_messages 剥离下划线前缀元字段、
内部字段不泄漏给模型 API、且原消息不被就地修改（DB 需保留 _ts）。
"""

from __future__ import annotations

from query.loop import _build_assistant_message
from query.services.api.llm import _build_messages
from tools.executor import ToolExecutionResult, tool_result_to_openai_message


def test_build_assistant_message_adds_ts():
    msg = _build_assistant_message(
        ["你好"],
        [{"id": "call_1", "function": {"name": "bash", "arguments": "{}"}}],
    )
    assert msg["role"] == "assistant"
    assert msg["content"] == "你好"
    assert msg["tool_calls"]
    assert isinstance(msg["_ts"], (int, float))
    assert msg["_ts"] > 0


def test_tool_result_message_adds_ts():
    result = ToolExecutionResult(tool_call_id="call_1", tool_name="bash", content="ok")
    msg = tool_result_to_openai_message(result)
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_1"
    assert msg["content"] == "ok"
    assert isinstance(msg["_ts"], (int, float))
    assert msg["_ts"] > 0


def test_build_messages_strips_underscore_fields():
    msgs = [
        {"role": "user", "content": "hi", "_ts": 123456.0},
        {"role": "assistant", "content": "yo", "_ts": 123457.0, "_foo": "bar"},
    ]
    out = _build_messages(msgs)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    # 原消息未被就地修改（持久化需保留 _ts）
    assert msgs[0]["_ts"] == 123456.0
    assert msgs[1]["_ts"] == 123457.0
