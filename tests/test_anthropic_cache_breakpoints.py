"""Anthropic 提示词缓存断点单元测试。

覆盖 `_to_anthropic_messages` 的 system block 数组化、
`_attach_cache_breakpoints` 的静态段断点、消息尾部滚动断点、
开关关闭回退与断点预算守卫。纯函数直测，不经网络层。
"""

from __future__ import annotations

import logging

from query.services.api.anthropic_llm import (
    _attach_cache_breakpoints,
    _to_anthropic_messages,
)


def _openai_msgs() -> list[dict]:
    """典型消息序列：静态 system + 动态 system + user + assistant(tool_calls) + tool 结果。"""
    return [
        {"role": "system", "content": "静态段合并结果"},
        {"role": "system", "content": "动态段：项目信息"},
        {"role": "user", "content": "你好"},
        {
            "role": "assistant",
            "content": "调用工具",
            "tool_calls": [
                {"id": "t1", "function": {"name": "Bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
    ]


# ---- _to_anthropic_messages：system 数组化 ----

def test_system_becomes_block_array():
    """多条 system 消息逐条映射为 text block，顺序保持，不再 join 成字符串。"""
    system_blocks, messages = _to_anthropic_messages(_openai_msgs())
    assert isinstance(system_blocks, list)
    assert [b["text"] for b in system_blocks] == ["静态段合并结果", "动态段：项目信息"]
    assert all(b["type"] == "text" for b in system_blocks)
    # 连续 tool 结果合并进一个 user 消息（既有行为不回归）
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][0]["type"] == "tool_result"


def test_no_system_returns_none():
    """无 system 消息时返回 None。"""
    system_blocks, _ = _to_anthropic_messages([{"role": "user", "content": "hi"}])
    assert system_blocks is None


# ---- _attach_cache_breakpoints：断点落位 ----

def test_breakpoints_attached_on_static_and_tail():
    """开启时：第一条 system block 与最后一条消息最后一个 block 各挂一个断点。"""
    system_blocks, messages = _to_anthropic_messages(_openai_msgs())
    payload = {"system": system_blocks, "messages": messages}
    total = _attach_cache_breakpoints(payload, enabled=True)

    assert total == 2
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    # 动态段（第二条 system）不挂
    assert "cache_control" not in payload["system"][1]
    # 最后一条消息是 tool_result 合并的 user 消息，断点在其最后一个 block 上
    last_blocks = payload["messages"][-1]["content"]
    assert last_blocks[-1]["cache_control"] == {"type": "ephemeral"}
    # 其余消息不带断点
    assert all(
        "cache_control" not in b
        for m in payload["messages"][:-1]
        for b in (m["content"] if isinstance(m["content"], list) else [])
    )


def test_string_content_converted_to_blocks():
    """最后一条消息 content 为字符串时，先转 block 数组再挂断点。"""
    payload = {
        "messages": [{"role": "user", "content": "纯文本"}],
    }
    total = _attach_cache_breakpoints(payload, enabled=True)
    assert total == 1
    blocks = payload["messages"][-1]["content"]
    assert blocks == [{"type": "text", "text": "纯文本", "cache_control": {"type": "ephemeral"}}]


def test_disabled_attaches_nothing():
    """开关关闭时请求体不含任何 cache_control，字符串 content 也不被改写。"""
    system_blocks, messages = _to_anthropic_messages(_openai_msgs())
    messages[-1]["content"] = "纯文本"  # 简化断言
    payload = {"system": system_blocks, "messages": messages}
    total = _attach_cache_breakpoints(payload, enabled=False)

    assert total == 0
    assert payload["messages"][-1]["content"] == "纯文本"
    assert all("cache_control" not in b for b in payload["system"])
    assert all(
        "cache_control" not in b
        for m in payload["messages"]
        for b in (m["content"] if isinstance(m["content"], list) else [])
    )


def test_budget_exceeded_skips_with_warning(caplog):
    """已有断点数达上限时全部跳过，并输出 warning。"""
    payload = {
        "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}] * 4,
        "messages": [{"role": "user", "content": "hi"}],
    }
    with caplog.at_level(logging.WARNING):
        total = _attach_cache_breakpoints(payload, enabled=True)

    assert total == 4
    # 消息 content 仍是字符串（预算耗尽时不做无谓的 block 转换，也不挂断点）
    assert payload["messages"][-1]["content"] == "hi"
    assert "上限" in caplog.text


def test_empty_messages_tolerated():
    """空 messages / 空 content 不抛错。"""
    assert _attach_cache_breakpoints({"messages": []}, enabled=True) == 0
    payload = {"messages": [{"role": "user", "content": []}]}
    assert _attach_cache_breakpoints(payload, enabled=True) == 0
