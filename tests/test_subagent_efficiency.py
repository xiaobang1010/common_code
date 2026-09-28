"""子代理执行效率改造验收用例：提示词/Notes/env 注入、Explore 白名单、
Bash 只读放行、grep include 语义与忽略规则、默认轮数。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


class _FakeTool:
    """白名单测试用的最小工具桩。"""

    def __init__(self, name: str):
        self.name = name

    def get_metadata(self):
        return SimpleNamespace(read_only=True)


def _all_tool_stubs() -> list[_FakeTool]:
    names = [
        "Bash", "Glob", "Grep", "Read", "WebFetch", "TodoWrite",
        "Write", "Edit", "Skill", "ShowWidget", "WidgetGuidelines",
        "PresentFiles", "TeamTaskList", "SendMessage", "Agent",
        "mcp__demo__probe",
    ]
    return [_FakeTool(n) for n in names]


class _FakeRegistry:
    """按 agent_id 返回带 agent_type 的任务桩。"""

    def __init__(self, agent_type: str | None):
        self._agent_type = agent_type

    def get(self, agent_id: str):
        if self._agent_type is None:
            return None
        return SimpleNamespace(agent_type=self._agent_type)


# ---------------------------------------------------------------------------
# 内置提示词与描述改写
# ---------------------------------------------------------------------------


def test_general_purpose_prompt_aligned():
    from tools.subagent.built_in_agents import find_agent_by_type

    prompt = find_agent_by_type("general-purpose").system_prompt
    assert prompt.startswith("You are an agent for Common Code CLI.")
    assert "Complete the task fully—don't gold-plate" in prompt
    assert "respond with a concise report" in prompt
    assert "NEVER proactively create documentation files" in prompt


def test_explore_prompt_aligned():
    from tools.subagent.built_in_agents import find_agent_by_type

    prompt = find_agent_by_type("Explore").system_prompt
    assert prompt.startswith("You are Common Code Explore,")
    assert "CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS" in prompt
    assert "Use Glob for broad file pattern matching" in prompt
    assert "spawn multiple parallel tool calls" in prompt


def test_when_to_use_aligned():
    from tools.subagent.built_in_agents import find_agent_by_type

    gp = find_agent_by_type("general-purpose").when_to_use
    assert gp.startswith("General-purpose agent for researching complex questions")
    explore = find_agent_by_type("Explore").when_to_use
    assert 'Specify search breadth: "medium"' in explore
    assert '"very thorough"' in explore


def test_agent_tool_prompt_contains_delegate_rule():
    from tools.subagent.agent_tool import build_agent_prompt

    text = build_agent_prompt()
    assert "Once you've delegated a search, don't also run it yourself" in text
    assert "search breadth" in text


# ---------------------------------------------------------------------------
# Notes / env 注入
# ---------------------------------------------------------------------------


def test_notes_and_env_injected(monkeypatch, tmp_path):
    from tools.subagent.context import build_subagent_system_prompt

    # 非 git 目录：分支行省略
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path))
    agent = AgentDefinition(
        agent_type="a", when_to_use="t", system_prompt="基础提示词"
    )
    prompt = build_subagent_system_prompt(agent, model="test-model")

    assert "Notes:" in prompt
    assert "please only use absolute file paths" in prompt
    assert "Do NOT Write report/summary/findings/analysis .md files" in prompt
    assert "<env>" in prompt
    assert f"Working directory: {tmp_path}" in prompt
    assert "Is directory a git repo: No" in prompt
    assert "Git branch:" not in prompt
    assert "You are powered by the model named test-model." in prompt

    # 顺序：代理提示词 → Notes → env
    assert prompt.index("基础提示词") < prompt.index("Notes:") < prompt.index("<env>")


def test_env_git_branch_included(monkeypatch, tmp_path):
    from pathlib import Path

    from tools.subagent.context import build_subagent_system_prompt

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path))

    agent = AgentDefinition(agent_type="a", when_to_use="t", system_prompt="p")
    prompt = build_subagent_system_prompt(agent)
    assert "Is directory a git repo: Yes" in prompt
    assert "Git branch: dev" in prompt
    # model 未传时省略模型名行
    assert "You are powered by the model named" not in prompt


def test_agents_md_after_env(monkeypatch, tmp_path):
    from tools.subagent.context import build_subagent_system_prompt

    (tmp_path / "AGENTS.md").write_text("工作区规范内容", encoding="utf-8")
    monkeypatch.setattr("server.paths.effective_root", lambda: str(tmp_path))

    agent = AgentDefinition(
        agent_type="a", when_to_use="t", system_prompt="p", inject_agents_md=True
    )
    prompt = build_subagent_system_prompt(agent)
    assert prompt.index("<env>") < prompt.index("工作区规范内容")


# ---------------------------------------------------------------------------
# Explore 工具白名单
# ---------------------------------------------------------------------------


def test_explore_tool_whitelist():
    from tools.subagent.built_in_agents import find_agent_by_type
    from tools.subagent.tools import resolve_agent_tools

    explore = find_agent_by_type("Explore")
    resolved = resolve_agent_tools(explore, _all_tool_stubs())
    names = {t.name for t in resolved}
    # 内置工具恰为六项白名单；MCP 工具按既有机制不受白名单限制、保留放行
    builtin_names = {n for n in names if not n.startswith("mcp__")}
    assert builtin_names == {"Bash", "Glob", "Grep", "Read", "WebFetch", "TodoWrite"}
    assert "mcp__demo__probe" in names


def test_general_purpose_keeps_wildcard():
    from tools.subagent.built_in_agents import find_agent_by_type
    from tools.subagent.tools import resolve_agent_tools

    gp = find_agent_by_type("general-purpose")
    names = {t.name for t in resolve_agent_tools(gp, _all_tool_stubs())}
    # 通配全量，仅移除 Agent（防递归）
    assert "Agent" not in names
    assert {"Bash", "Write", "Edit", "Skill", "mcp__demo__probe"} <= names


# ---------------------------------------------------------------------------
# Explore Bash 只读放行
# ---------------------------------------------------------------------------


def _bash_input(command: str) -> SimpleNamespace:
    return SimpleNamespace(command=command)


def _decision(monkeypatch, agent_type: str | None, command: str):
    from query.engine import _explore_bash_decision

    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry",
        lambda: _FakeRegistry(agent_type),
    )
    tool = SimpleNamespace(name="Bash")
    context = SimpleNamespace(tool_use_id="agent_test")
    return _explore_bash_decision(tool, _bash_input(command), context)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git log --oneline -5",
        "git status",
        "git diff HEAD~1",
        "git remote -v",
        "cat README.md",
        "find . -name '*.py'",
        "ls && git status",
    ],
)
def test_explore_bash_readonly_allowed(monkeypatch, command):
    assert _decision(monkeypatch, "Explore", command) == {"decision": "allow"}


@pytest.mark.parametrize(
    "command",
    [
        "mkdir foo",
        "git commit -m x",
        "git remote add origin http://x",
        "cat a.txt > b.txt",
        "cat a.txt | grep x",
        "ls && mkdir foo",
        "echo hi >> log.txt",
        "tee out.txt",
        "npm install",
    ],
)
def test_explore_bash_write_denied(monkeypatch, command):
    decision = _decision(monkeypatch, "Explore", command)
    assert decision is not None and decision["decision"] == "deny"
    assert "read-only" in decision["reason"]


def test_explore_bash_scoped_to_explore(monkeypatch):
    # 非 Explore（general-purpose）与非子代理上下文都不受白名单影响
    assert _decision(monkeypatch, "general-purpose", "mkdir foo") is None
    assert _decision(monkeypatch, None, "mkdir foo") is None

    from query.engine import _explore_bash_decision

    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry",
        lambda: _FakeRegistry("Explore"),
    )
    # 非 Bash 工具不判定
    assert _explore_bash_decision(
        SimpleNamespace(name="Grep"), _bash_input("x"), SimpleNamespace(tool_use_id="agent_t")
    ) is None
    # 主循环上下文（tool_use_id 为空）不判定
    assert _explore_bash_decision(
        SimpleNamespace(name="Bash"), _bash_input("ls"), SimpleNamespace(tool_use_id="")
    ) is None


@pytest.mark.asyncio
async def test_default_permission_check_explore_early_allow(monkeypatch):
    from query.engine import _default_permission_check

    monkeypatch.setattr(
        "tools.subagent.registry.get_subagent_registry",
        lambda: _FakeRegistry("Explore"),
    )
    decision = await _default_permission_check(
        SimpleNamespace(name="Bash"),
        _bash_input("git log --oneline"),
        SimpleNamespace(tool_use_id="agent_t"),
    )
    assert decision == {"decision": "allow"}


# ---------------------------------------------------------------------------
# Grep include 语义与忽略规则
# ---------------------------------------------------------------------------


def test_match_include_fnmatch_semantics():
    from tools.implementations.grep_tool.handler import _match_include

    assert _match_include("test_foo.py", "test_*.py") is True
    assert _match_include("main.py", "test_*.py") is False
    assert _match_include("a.ts", "*.ts,*.tsx") is True
    assert _match_include("a.tsx", "*.ts,*.tsx") is True
    assert _match_include("a.py", "*.ts,*.tsx") is False
    assert _match_include("x.md", None) is True
    # 修复点：非 * 前缀模式不再放行所有文件
    assert _match_include("anything.txt", "cfg?.ini") is False
    assert _match_include("cfg1.ini", "cfg?.ini") is True


def test_excluded_dirs_cover_caches():
    from tools.implementations.grep_tool.handler import _EXCLUDED_DIRS

    assert {".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage"} <= _EXCLUDED_DIRS


def test_filter_git_ignored(monkeypatch, tmp_path):
    from pathlib import Path

    from tools.implementations.grep_tool import handler as grep_handler

    files = [tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "outside.txt"]
    monkeypatch.setattr(
        "server.git_ignore.ignored_names",
        lambda root, paths: {"b.txt"} if "b.txt" in paths else set(),
    )
    kept = grep_handler._filter_git_ignored(files, tmp_path)
    # 命中忽略的剔除；工作区外路径（relative_to 失败）不在判定集，原样保留
    assert kept == [tmp_path / "a.txt", tmp_path / "outside.txt"]

    # 判定失败（异常）返回原列表
    def _boom(root, paths):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr("server.git_ignore.ignored_names", _boom)
    assert grep_handler._filter_git_ignored(files, tmp_path) == files


# ---------------------------------------------------------------------------
# 默认轮数
# ---------------------------------------------------------------------------


def test_budget_defaults_fallback_turns(monkeypatch):
    from tools.subagent import lifecycle
    from tools.subagent.context import create_subagent_context

    monkeypatch.setattr(lifecycle, "_get_subagents_config", lambda: None)
    agent = AgentDefinition(agent_type="a", when_to_use="t", system_prompt="p")
    ctx = create_subagent_context(
        parent_context=None, agent_def=agent, main_loop_model="m", prompt="t"
    )
    lifecycle._apply_budget_defaults(ctx, agent)
    assert ctx.max_turns == 4


def test_config_default_max_turns_is_four():
    from startup.config.types import SubagentsConfig

    assert SubagentsConfig().max_turns_default == 4
