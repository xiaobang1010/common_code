"""自定义子代理加载器与 Profile 字段测试。"""

from __future__ import annotations

import pytest

from tools.protocol import build_tool
from tools.subagent.loader import (
    _extract_frontmatter,
    find_custom_agent,
    load_custom_agents,
)
from tools.subagent.tools import resolve_agent_tools
from tools.subagent.types import AgentDefinition


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def agent_dirs(tmp_path, monkeypatch):
    """把用户级/项目级代理目录指到临时目录。"""
    user_dir = tmp_path / "user" / "agents"
    proj_dir = tmp_path / "proj" / "agents"
    import tools.subagent.loader as loader_mod
    monkeypatch.setattr(loader_mod, "get_user_agents_dir", lambda: user_dir)
    monkeypatch.setattr(loader_mod, "get_project_agents_dir", lambda: proj_dir)
    return user_dir, proj_dir


def test_extract_frontmatter_fields():
    """frontmatter 解析：字符串/列表/布尔/多行列表。"""
    text = """---
name: reviewer
description: 代码审查
tools: [Read, Grep]
disallowed-tools:
  - Bash
  - Write
max-turns: 5
background: true
---

你是审查员。
"""
    fields, body = _extract_frontmatter(text)
    assert fields["name"] == "reviewer"
    assert fields["tools"] == ["Read", "Grep"]
    assert fields["disallowed_tools"] == ["Bash", "Write"]
    assert fields["max_turns"] == "5"
    assert fields["background"] is True
    assert body.strip() == "你是审查员。"


def test_load_valid_agent(agent_dirs):
    """合法 .md 可加载为 AgentDefinition，正文作系统提示词。"""
    user_dir, _ = agent_dirs
    _write(user_dir / "reviewer.md", """---
name: reviewer
description: 审查代码变更
tools: [Read, Grep]
model: glm-4.7
max-turns: 5
---

你是审查员。
""")
    agents, diagnostics = load_custom_agents()
    assert diagnostics == []
    reviewer = next(a for a in agents if a.agent_type == "reviewer")
    assert reviewer.source == "user"
    assert reviewer.tools == ["Read", "Grep"]
    assert reviewer.model == "glm-4.7"
    assert reviewer.max_turns == 5
    assert reviewer.system_prompt.strip() == "你是审查员。"
    assert "Agent" in reviewer.disallowed_tools  # 防递归自动加


def test_missing_frontmatter_diagnosed(agent_dirs):
    """缺 frontmatter 返回诊断码，不静默丢弃。"""
    user_dir, _ = agent_dirs
    _write(user_dir / "broken.md", "只有正文没有元数据")
    agents, diagnostics = load_custom_agents()
    assert agents == []
    assert diagnostics[0]["code"] == "agent_missing_frontmatter"
    assert "broken.md" in diagnostics[0]["file"]


def test_missing_required_fields_diagnosed(agent_dirs):
    """缺 name/description 返回诊断并指明缺失字段。"""
    user_dir, _ = agent_dirs
    _write(user_dir / "nofields.md", "---\ndescription: 缺名字\n---\n正文")
    _, diagnostics = load_custom_agents()
    assert diagnostics[0]["code"] == "agent_missing_fields"
    assert "name" in diagnostics[0]["message"]


def test_project_permission_mode_stripped(agent_dirs):
    """项目级 permission_mode 被剥离（防提权），用户级保留。"""
    user_dir, proj_dir = agent_dirs
    _write(proj_dir / "escalate.md", """---
name: escalate
description: 项目级提权尝试
permission-mode: bypassPermissions
---

正文
""")
    _write(user_dir / "trusted.md", """---
name: trusted
description: 用户级配置
permission-mode: read-only
---

正文
""")
    agents, _ = load_custom_agents()
    escalate = next(a for a in agents if a.agent_type == "escalate")
    assert escalate.permission_mode is None  # 项目级被剥离
    trusted = next(a for a in agents if a.agent_type == "trusted")
    assert trusted.permission_mode == "read-only"


def test_user_overrides_project_same_name(agent_dirs):
    """同名时用户级覆盖项目级。"""
    user_dir, proj_dir = agent_dirs
    _write(proj_dir / "dual.md", "---\nname: dual\ndescription: 项目版\n---\n项目正文")
    _write(user_dir / "dual.md", "---\nname: dual\ndescription: 用户版\n---\n用户正文")
    agents, _ = load_custom_agents()
    dual = next(a for a in agents if a.agent_type == "dual")
    assert dual.when_to_use == "用户版"
    assert dual.source == "user"


def test_find_custom_agent(agent_dirs):
    """find_custom_agent 按类型命中。"""
    user_dir, _ = agent_dirs
    _write(user_dir / "reviewer.md", "---\nname: reviewer\ndescription: 审查\n---\n正文")
    assert find_custom_agent("reviewer") is not None
    assert find_custom_agent("nobody") is None


def test_builtin_priority_over_custom(agent_dirs, monkeypatch):
    """内置类型优先于自定义同名（不可覆盖内置）。"""
    from tools.subagent import built_in_agents
    user_dir, _ = agent_dirs
    _write(user_dir / "Explore.md", "---\nname: Explore\ndescription: 假 Explore\n---\n正文")
    found = built_in_agents.find_agent_by_type("Explore")
    assert found is not None and found.source == "built-in"


def test_read_only_permission_mode_filters_tools():
    """permission_mode=read-only 只保留只读工具。"""
    async def _noop(_inp, _ctx):
        return None

    tools = [
        build_tool(name="Read", description="读", input_schema=None, prompt="", execute=_noop, is_read_only=True),  # type: ignore[arg-type]
        build_tool(name="Write", description="写", input_schema=None, prompt="", execute=_noop),  # type: ignore[arg-type]
    ]
    ro_def = AgentDefinition(
        agent_type="ro", when_to_use="只读", permission_mode="read-only"
    )
    resolved = resolve_agent_tools(ro_def, tools)
    assert [t.name for t in resolved] == ["Read"]

    inherit_def = AgentDefinition(agent_type="inh", when_to_use="继承")
    assert len(resolve_agent_tools(inherit_def, tools)) == 2  # 未声明则不裁剪


@pytest.mark.asyncio
async def test_background_profile_forces_background_mode(agent_dirs, monkeypatch):
    """profile background:true 自动以后台模式启动。"""
    user_dir, _ = agent_dirs
    _write(user_dir / "batcher.md", """---
name: batcher
description: 批处理
background: true
---

正文
""")
    import query.services.api.client as api_client
    monkeypatch.setattr(api_client, "get_default_model", lambda: "m")

    import tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "get_tools", lambda *a, **k: [])

    launched = {}

    from tools.subagent import background as bg_mod

    def fake_launch(ctx, tools, system_prompt, *, description="", parent_session_id=None):
        launched["agent_id"] = ctx.agent_id
        from tools.subagent.registry import SubagentTaskRegistry, SubagentTask
        return SubagentTask(agent_id=ctx.agent_id)

    monkeypatch.setattr(bg_mod, "launch_background_subagent", fake_launch)

    import tools.subagent.agent_tool as agent_tool_mod
    monkeypatch.setattr(
        "tools.subagent.agent_tool.launch_background_subagent", None, raising=False
    )
    # agent_tool 内部是函数级导入 from tools.subagent.background import launch_background_subagent
    # 打在 background 模块上即可
    agent_tool_mod_ref = agent_tool_mod

    from tools.protocol import ToolUseContext
    result = await agent_tool_mod_ref._execute(
        agent_tool_mod.AgentInput(description="批处理", prompt="跑", subagent_type="batcher"),
        ToolUseContext(),
    )
    assert "agent_id" in launched
    assert result.metadata["status"] == "async_launched"
