"""工具描述接线回归测试。

守两条线：
1. 序列化到模型的 function description 必须等于工具的完整使用说明 prompt
   （历史上这里错发了短摘要，模型看不到使用说明）；
2. 关键工具的描述必须含住操作手册要点，防止后续改动悄悄把内容删薄。
"""

from __future__ import annotations

from tools import get_tools
from tools.utils.schema import tool_to_openai_schema

# prompt 最短长度护栏：低于此字数说明说明被删薄了
_MIN_PROMPT_CHARS = 60


def _serialized_description(tool) -> str:
    return tool_to_openai_schema(tool)["function"]["description"]


def test_every_tool_sends_full_prompt():
    """每个工具的模型侧 description 必须等于其 prompt（接线不断）。"""
    for tool in get_tools():
        assert tool.prompt, f"{tool.name} 缺 prompt"
        assert len(tool.prompt) >= _MIN_PROMPT_CHARS, f"{tool.name} prompt 过短"
        assert _serialized_description(tool) == tool.prompt, f"{tool.name} 未发送完整 prompt"


def test_description_field_kept_as_short_summary():
    """description 字段保留为短摘要，仍应非空（UI/日志语义不变）。"""
    for tool in get_tools():
        assert tool.description and tool.description.strip(), f"{tool.name} 缺短摘要"


def test_bash_prompt_covers_manual_points():
    """Bash 说明书必须含住最容易出事故的四类规则。"""
    prompt = _serialized_description(next(t for t in get_tools() if t.name == "Bash"))
    # 专用工具优先：禁 shell 文件操作
    assert "Read" in prompt and "cat" in prompt
    assert "Glob" in prompt and "Grep" in prompt
    # 超时语义：长命令必须显式给足 timeout
    assert "timeout" in prompt and "2 minutes" in prompt
    # git 安全：禁绕过钩子
    assert "--no-verify" in prompt
    # 重试纪律：禁 sleep 轮询
    assert "sleep" in prompt


def test_read_prompt_states_segmented_read():
    """Read 必须交代默认行数上限与分段读取口径。"""
    prompt = _serialized_description(next(t for t in get_tools() if t.name == "Read"))
    assert "2000" in prompt
    assert "offset" in prompt


def test_edit_prompt_states_uniqueness_and_baseline():
    """Edit 必须交代唯一匹配、先读后改、失败对症处理。"""
    prompt = _serialized_description(next(t for t in get_tools() if t.name == "Edit"))
    assert "not unique" in prompt
    assert "Read" in prompt
    assert "replace_all" in prompt


def test_agent_prompt_includes_listing():
    """Agent 描述应动态渲染可用代理清单。"""
    prompt = _serialized_description(next(t for t in get_tools() if t.name == "Agent"))
    assert "general-purpose" in prompt
    assert "Explore" in prompt
    assert "returned only to you" in prompt  # 子代理结果需转述
