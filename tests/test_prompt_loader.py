"""提示词模板加载器测试：渲染、变量注入、残留占位符防线。"""

from __future__ import annotations

import pytest
from jinja2 import TemplateError

from prompts.loader import load_tool_prompt, render_prompt
from prompts.system.builder import get_system_prompt_sections
from prompts.system.sections import _SYSTEM_STATIC_SECTION_NAMES


def test_tool_prompt_renders_without_residue():
    """每个工具模板渲染后不得残留 Jinja 语法或模板变量。"""
    import os

    tools_dir = os.path.join(os.path.dirname(__file__), "..", "prompts", "templates", "tools")
    keys = [fn[:-3] for fn in os.listdir(tools_dir) if fn.endswith(".j2")]
    assert len(keys) >= 15  # 内置工具说明已全部模板化
    for key in keys:
        rendered = load_tool_prompt(key, agents=[])
        assert "{{" not in rendered and "{%" not in rendered, f"{key} 存在未渲染占位符"
        assert rendered.strip(), f"{key} 渲染为空"


def test_agent_template_renders_listing():
    """Agent 模板按传入清单动态渲染代理类型行。"""
    listing = [
        {"type": "general-purpose", "when_to_use": "通用任务", "tools": "*"},
        {"type": "Explore", "when_to_use": "只读检索", "tools": "Read, Grep"},
    ]
    rendered = load_tool_prompt("agent", agents=listing)
    assert "- general-purpose: 通用任务 (Tools: *)" in rendered
    assert "- Explore: 只读检索 (Tools: Read, Grep)" in rendered


def test_agent_template_requires_agents_var():
    """agents 变量缺失必须报错，而不是渲染出残缺提示词。"""
    with pytest.raises(TemplateError):
        render_prompt("tools/agent.j2")


def test_static_sections_assembled_in_order():
    """静态段按声明顺序拼接，且全部来自 .j2 模板。"""
    sections = get_system_prompt_sections()
    static = [s for s in sections if s.name == "static_sections"]
    assert len(static) == 1
    assert static[0].cache_scope == "static"
    # 各段首行标题按声明顺序出现
    pos = -1
    for name in _SYSTEM_STATIC_SECTION_NAMES:
        header = render_prompt(f"system/{name}.j2").splitlines()[0]
        found = static[0].content.find(header)
        assert found > pos, f"{name} 段顺序错乱"
        pos = found
