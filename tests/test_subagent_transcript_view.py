"""子代理 transcript 视图模式测试 — 执行轨迹展示的重建口径。

真实落盘顺序是工具结果行先于其 assistant+tool_calls 行（循环先产出
结果、后写 assistant 消息），用例按该顺序构造，锁定：
- 默认模式：全部 tool_calls 被剥离（既有合法序列语义不回退）
- 视图模式：已完成调用不标 pending、悬挂调用标 pending、过程字段带出
"""

from __future__ import annotations

import json

from tools.subagent import transcript


def _write_transcript(tmp_path, entries: list[dict]) -> None:
    """按给定顺序落一份 JSONL（parentUuid 链按写入顺序相接）。"""
    agent_dir = tmp_path / "agent_view1"
    agent_dir.mkdir(parents=True, exist_ok=True)
    prev: str | None = None
    lines = []
    for i, e in enumerate(entries):
        row = {
            "uuid": f"u{i}",
            "parentUuid": prev,
            "agentId": "agent_view1",
            "isSidechain": True,
            "timestamp": 1000.0 + i,
        }
        row.update(e)
        lines.append(json.dumps(row, ensure_ascii=False))
        prev = row["uuid"]
    (agent_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _entries() -> list[dict]:
    """一轮真实形态的消息：两个工具结果行在前，携带三个调用的 assistant 行在后
    （第三个调用无结果，模拟进行中/悬挂）。"""
    return [
        {"role": "tool", "content": "结果甲", "tool_call_id": "call_a"},
        {"role": "tool", "content": "结果乙", "tool_call_id": "call_b"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_a", "function": {"name": "Grep", "arguments": "{}"}},
                {"id": "call_b", "function": {"name": "Read", "arguments": "{}"}},
                {"id": "call_c", "function": {"name": "Bash", "arguments": "{}"}},
            ],
            "reasoning": "先搜后读",
            "reasoning_ms": 3200,
            "ts": 1700000000000,
        },
        {"role": "assistant", "content": "结论正文"},
    ]


def test_default_mode_strips_all_tool_calls(monkeypatch, tmp_path):
    """默认模式：结果行在前的真实行序下，全部调用被剥离、空正文 assistant 行丢弃。"""
    monkeypatch.setattr(transcript, "_get_subagents_base_dir", lambda: tmp_path)
    _write_transcript(tmp_path, _entries())

    messages = transcript.get_agent_transcript("agent_view1")
    assert messages is not None
    # assistant+tool_calls 行剥离调用后正文为空，整行被过滤，只剩结果与正文
    assert [m["role"] for m in messages] == ["tool", "tool", "assistant"]
    assert all("tool_calls" not in m for m in messages)
    assert messages[-1]["content"] == "结论正文"


def test_view_mode_keeps_calls_and_marks_pending(monkeypatch, tmp_path):
    """视图模式：已完成调用不标 pending，悬挂调用标 pending，行序无关。"""
    monkeypatch.setattr(transcript, "_get_subagents_base_dir", lambda: tmp_path)
    _write_transcript(tmp_path, _entries())

    messages = transcript.get_agent_transcript("agent_view1", for_view=True)
    assert messages is not None
    assistant = next(m for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
    calls = {tc["id"]: tc for tc in assistant["tool_calls"]}
    assert "pending" not in calls["call_a"]
    assert "pending" not in calls["call_b"]
    assert calls["call_c"]["pending"] is True


def test_view_mode_surfaces_reasoning_fields(monkeypatch, tmp_path):
    """视图模式带出 reasoning/reasoning_ms/ts；默认模式不复制这些键。"""
    monkeypatch.setattr(transcript, "_get_subagents_base_dir", lambda: tmp_path)
    _write_transcript(tmp_path, _entries())

    view = transcript.get_agent_transcript("agent_view1", for_view=True)
    assistant = next(m for m in view if m.get("reasoning"))
    assert assistant["reasoning"] == "先搜后读"
    assert assistant["reasoning_ms"] == 3200
    assert assistant["ts"] == 1700000000000

    default = transcript.get_agent_transcript("agent_view1")
    assert all("reasoning" not in m and "ts" not in m for m in default)


def test_view_mode_tolerates_legacy_transcript(monkeypatch, tmp_path):
    """旧转录无 reasoning/ts 字段：视图模式正常重建，相关键缺省。"""
    monkeypatch.setattr(transcript, "_get_subagents_base_dir", lambda: tmp_path)
    _write_transcript(
        tmp_path,
        [
            {"role": "assistant", "content": "旧格式结论"},
        ],
    )

    messages = transcript.get_agent_transcript("agent_view1", for_view=True)
    assert messages is not None
    assert messages[0]["content"] == "旧格式结论"
    assert "reasoning" not in messages[0]
    assert "ts" not in messages[0]
