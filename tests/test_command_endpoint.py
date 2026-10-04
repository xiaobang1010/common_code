"""斜杠命令端点测试 — /api/command 三分支与标题提取。

用 conftest 的 workspace fixture 隔离工作区；技能命中走内置 spec 技能，
disable-model-invocation 场景用临时注册技能并在 teardown 清理，避免污染
其他用例。
"""

from __future__ import annotations

import asyncio

import pytest

from tools.commands.commands import clear_commands
from tools.skills.bundled import (
    _bundled_skills,
    find_skill_by_name,
    register_bundled_skill,
)
from tools.skills.types import Skill

from server.routers.commands.routes import run_command


@pytest.fixture
def cleanup_commands():
    """命令注册表是模块级状态，测后清理避免影响其他用例。"""
    yield
    clear_commands()


def _run(body: dict) -> dict:
    return asyncio.run(run_command(body))


def test_skill_hit_returns_rewrite_prompt(workspace, cleanup_commands):
    """可模型调用技能命中：返回重写提示（不含正文），任务描述带在 User request。"""
    result = _run({"command": "/spec build a todo module"})

    assert result["is_skill"] is True
    assert result["skill_name"] == "spec"
    prompt = result["skill_prompt"]
    assert prompt.startswith("Use the skill named `spec` for this turn.")
    assert 'First call the `Skill` tool with skill="spec"' in prompt
    assert prompt.endswith("User request: build a todo module")
    # 正文不再内联：技能正文特征串不出现
    assert "按 spec 驱动开发模式工作" not in prompt


def test_skill_hit_without_args_has_empty_guidance(workspace, cleanup_commands):
    """/spec 不带描述：引导行存在且位于 User request 行之前。"""
    result = _run({"command": "/spec"})

    prompt = result["skill_prompt"]
    guide = "User request is empty: ask the user what they want to do first"
    assert guide in prompt
    assert prompt.index(guide) < prompt.index("User request:")


def test_disable_model_invocable_skill_falls_back_to_inline(workspace, cleanup_commands):
    """disable-model-invocation 技能：回退 system-reminder 内联正文（恒含任务段）。"""
    original = find_skill_by_name("spec")
    locked = Skill(
        name="locked-skill",
        description="临时注册：用户可触发但禁止模型调用",
        content="正文内容",
        disable_model_invocation=True,
        source="bundled",
    )
    register_bundled_skill(locked)
    try:
        result = _run({"command": "/locked-skill 写个爬虫"})

        assert result["is_skill"] is True
        prompt = result["skill_prompt"]
        assert prompt.startswith("<system-reminder>")
        assert "## 用户任务" in prompt
        assert "写个爬虫" in prompt
    finally:
        _bundled_skills.remove(locked)
        assert find_skill_by_name("locked-skill") is None
        assert original is not None  # 防 unused 告警


def test_regular_command_returns_output(workspace, cleanup_commands):
    """普通命令命中（/help）：返回 output，无 is_skill。"""
    result = _run({"command": "/help"})

    assert "is_skill" not in result
    assert "Available commands" in result["output"]


def test_unknown_command_returns_hint(workspace, cleanup_commands):
    """未知命令：返回提示文本。"""
    result = _run({"command": "/notexist"})

    assert "is_skill" not in result
    assert "未知命令" in result["output"]


def test_extract_session_title(workspace, cleanup_commands):
    """标题提取四场景：重写提示取任务段 / 空任务回退 /spec / 不匹配走原逻辑。"""
    from server.routers.chat.routes import _extract_session_title

    rewrite = (
        "Use the skill named `spec` for this turn.\n"
        'First call the `Skill` tool with skill="spec" before doing the task.\n'
        "After the skill content is loaded, follow its instructions and continue.\n"
        "\n"
        "User request: 分析这个项目"
    )
    assert _extract_session_title(rewrite) == "分析这个项目"

    rewrite_empty = rewrite.replace(
        "User request: 分析这个项目",
        "User request is empty: ask the user what they want to do first; do NOT invent a task.\nUser request: ",
    )
    assert _extract_session_title(rewrite_empty) == "/spec"

    pseudo = "Use the skill named `spec` 我随便说点啥，形状不完整"
    assert _extract_session_title(pseudo) == pseudo[:40]

    assert _extract_session_title("修一下登录页的样式") == "修一下登录页的样式"
