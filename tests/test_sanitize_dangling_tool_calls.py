"""sanitize_dangling_tool_calls 单元测试。

覆盖中断收尾与输出超限恢复两类残缺历史的补齐行为，以及合法历史、
边界输入（空列表、无 assistant、多条 tool_calls 部分缺失）的原样语义。
"""

from __future__ import annotations

from query.utils.messages import ABORTED_TOOL_RESULT_CONTENT, sanitize_dangling_tool_calls


def _assistant(tool_call_ids: list[str], content: str = "") -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"id": i, "function": {"name": "Bash", "arguments": "{}"}} for i in tool_call_ids],
    }


def _tool(tool_call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def test_empty_and_no_assistant_passthrough():
    """空列表与不含 assistant 的历史原样返回。"""
    assert sanitize_dangling_tool_calls([]) == []
    plain = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    result = sanitize_dangling_tool_calls(plain)
    assert result == plain
    assert result is not plain  # 返回副本，不改传入列表


def test_dangling_tail_single_tool_call():
    """中断典型形态：历史以 assistant(tool_calls) 结尾 → 末尾补合成结果。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1"]),
    ]
    result = sanitize_dangling_tool_calls(messages)
    assert result[-1]["role"] == "tool"
    assert result[-1]["tool_call_id"] == "t1"
    assert result[-1]["content"] == ABORTED_TOOL_RESULT_CONTENT
    assert messages == [{"role": "user", "content": "A"}, _assistant(["t1"])]  # 传入列表未变


def test_dangling_tail_partial_results():
    """多条 tool_calls 部分缺失：只补缺失 id，排在既有结果之后。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1", "t2", "t3"]),
        _tool("t1"),
        _tool("t3"),
    ]
    result = sanitize_dangling_tool_calls(messages)
    assert [m["tool_call_id"] for m in result if m["role"] == "tool"] == ["t1", "t3", "t2"]
    assert result[-1]["content"] == ABORTED_TOOL_RESULT_CONTENT


def test_dangling_mid_after_user():
    """输出超限恢复形态：assistant(tool_calls) 后直接跟 user → 在 user 前补结果。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1"]),
        {"role": "user", "content": "Output token limit hit. Resume directly"},
        {"role": "assistant", "content": "继续"},
    ]
    result = sanitize_dangling_tool_calls(messages)
    # 合成结果插在恢复 user 消息之前，恢复后的正常回复原样保留
    assert result[2]["role"] == "tool"
    assert result[2]["tool_call_id"] == "t1"
    assert result[3]["role"] == "user"
    assert result[4] == {"role": "assistant", "content": "继续"}


def test_dangling_mid_partial_then_new_turn():
    """中间残缺且部分结果存在：合成结果插在下一条非 tool 消息之前。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1", "t2"]),
        _tool("t1"),
        {"role": "user", "content": "B"},
    ]
    result = sanitize_dangling_tool_calls(messages)
    assert [m["tool_call_id"] for m in result if m["role"] == "tool"] == ["t1", "t2"]
    assert result[3]["tool_call_id"] == "t2"
    assert result[4]["role"] == "user"


def test_valid_history_unchanged_content():
    """合法历史（每条 tool_calls 都有齐全结果）返回内容一致的副本。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1", "t2"]),
        _tool("t2"),
        _tool("t1"),
        {"role": "assistant", "content": "完成"},
        {"role": "user", "content": "B"},
        _assistant(["t3"]),
        _tool("t3"),
        {"role": "assistant", "content": "好"},
    ]
    assert sanitize_dangling_tool_calls(messages) == messages


def test_multiple_turns_mixed():
    """多轮混合：合法轮次与残缺轮次并存，各自独立补齐。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1"]),
        _tool("t1"),
        {"role": "assistant", "content": "第一轮完成"},
        {"role": "user", "content": "B"},
        _assistant(["t2"]),
        # t2 结果缺失（中断）
        _assistant(["t3"]),
        # t3 结果缺失（输出超限恢复丢弃）
        {"role": "user", "content": "Output token limit hit. Resume directly"},
    ]
    result = sanitize_dangling_tool_calls(messages)
    tools = [m for m in result if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["t1", "t2", "t3"]
    # 合成结果紧跟在各自悬空 assistant 之后的正确位置：
    # t2 补在 t3 的 assistant 前，t3 补在恢复 user 前
    assert result[6]["tool_call_id"] == "t2"
    assert result[7]["role"] == "assistant"  # t3 的 assistant
    assert result[8]["tool_call_id"] == "t3"
    assert result[9]["role"] == "user"


def test_unknown_tool_result_kept():
    """无主 tool 结果（孤儿）保持原样，不参与补齐逻辑。"""
    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1"]),
        _tool("orphan-id"),
    ]
    result = sanitize_dangling_tool_calls(messages)
    assert result[2]["tool_call_id"] == "orphan-id"
    assert result[3]["tool_call_id"] == "t1"
    assert result[3]["content"] == ABORTED_TOOL_RESULT_CONTENT


def test_tool_calls_without_ids_ignored():
    """tool_calls 项缺 id 的异常数据不进入待补集合，不抛错。"""
    messages = [
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "Bash", "arguments": "{}"}}]},
        {"role": "user", "content": "B"},
    ]
    result = sanitize_dangling_tool_calls(messages)
    assert result == messages


def test_build_api_request_sanitizes_history():
    """build_api_request 收口：悬空历史在发出的请求里被补齐（7b 恢复分支场景）。"""
    from query.utils.api import build_api_request

    messages = [
        {"role": "user", "content": "A"},
        _assistant(["t1"]),
        {"role": "user", "content": "Output token limit hit. Resume directly"},
    ]
    request = build_api_request(messages=messages, system_prompt=[], tools=[], model="m")
    seq = [(m["role"], m.get("tool_call_id")) for m in request["messages"]]
    # 请求侧序列合法：t1 的合成结果在恢复 user 消息之前
    assert seq == [
        ("user", None),
        ("assistant", None),
        ("tool", "t1"),
        ("user", None),
    ]
    assert request["messages"][2]["content"] == ABORTED_TOOL_RESULT_CONTENT
