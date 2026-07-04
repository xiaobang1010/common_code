"""SkillTool — 统一的 Skill 工具入口。

Skill 不是独立的 Tool，而是通过这个单一 SkillTool 包装暴露给 LLM。
LLM 根据 skill_listing 里的 name + description + when_to_use 判断该不该调用，
然后发起 Skill(skill="commit", args="...") 工具调用。

execute 内部：查找 skill → 校验 → 调 resolve_prompt 获取正文 →
返回 ToolResult（content 是简短确认，new_messages 是 skill 正文注入对话，
context_modifier 注入 allowed_tools 权限）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool
from tools.skills.bundled import find_skill_by_name
from tools.skills.types import Skill


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class SkillInput(BaseModel):
    """Skill 工具输入。"""

    skill: str
    args: str = ""


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------


SKILL_TOOL_PROMPT = """\
执行指定的 Skill（能力包）。

使用说明：
- skill 参数是 skill 名称（在 skill_listing 中列出）
- args 是可选的参数字符串，会传递给 skill
- 根据 skill_listing 中的 when_to_use 判断是否匹配用户请求
- 匹配时必须在生成其他响应之前先调用此工具
"""


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


def _validate_input(inp: SkillInput, _context: ToolUseContext) -> dict[str, Any]:
    """校验 skill 名称非空。"""
    if not inp.skill.strip():
        return {"result": False, "message": "skill 名称不能为空"}
    return {"result": True}


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: SkillInput, _context: ToolUseContext) -> ToolResult:
    """执行 skill 调用。

    流程：
    1. 按名称查找 skill
    2. 校验 skill 存在且允许模型调用
    3. 调 resolve_prompt 获取正文
    4. 返回 ToolResult：
       - content: 简短确认 "Launching skill: {name}"
       - new_messages: skill 正文作为 user 消息（system-reminder 包裹）
       - context_modifier: allowed_tools 权限注入
    """
    # 1. 查找 skill
    skill = find_skill_by_name(inp.skill)
    if skill is None:
        return ToolResult(
            content=f"Skill not found: {inp.skill}",
            is_error=True,
        )

    # 2. 校验允许模型调用
    if not skill.is_model_invocable():
        return ToolResult(
            content=f"Skill '{inp.skill}' cannot be invoked by the model",
            is_error=True,
        )

    # 3. 获取正文
    try:
        prompt_content = skill.resolve_prompt(inp.args)
    except Exception as e:
        return ToolResult(
            content=f"Failed to get skill prompt: {e}",
            is_error=True,
        )

    if not prompt_content.strip():
        return ToolResult(
            content=f"Skill '{inp.skill}' has empty content",
            is_error=True,
        )

    # 4. 构建返回
    # skill 正文包装成 system-reminder 格式的 user 消息
    new_messages: list[dict] = [
        {
            "role": "user",
            "content": (
                "<system-reminder>\n"
                f"{prompt_content}\n"
                "</system-reminder>\n"
            ),
        }
    ]

    # context_modifier：注入 allowed_tools 权限
    context_modifier: dict = {}
    if skill.allowed_tools:
        context_modifier["allowed_tools"] = skill.allowed_tools

    return ToolResult(
        content=f"Launching skill: {skill.name}",
        is_error=False,
        new_messages=new_messages,
        context_modifier=context_modifier if context_modifier else None,
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_skill_tool() -> Tool:
    """返回 SkillTool 实例。"""
    return build_tool(
        name="Skill",
        description="Execute a skill by name",
        input_schema=SkillInput,
        execute=_execute,
        prompt=SKILL_TOOL_PROMPT,
        validate_input=_validate_input,
        is_read_only=True,
    )
