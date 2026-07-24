"""系统提示词构建。

定义系统提示词的段结构、各段静态内容、以及按需组装逻辑。
"""

from __future__ import annotations

import platform as _platform
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# SystemPromptSection - 系统提示段落
# ---------------------------------------------------------------------------

@dataclass
class SystemPromptSection:
    """系统提示词段。"""

    content: str
    cache_scope: str | None  # "global" / "static" / None
    name: str


# ---------------------------------------------------------------------------
# 核心提示词内容
# ---------------------------------------------------------------------------

# 归因头：标识客户端类型和运行平台
_ATTRIBUTION_HEADER = (
    f"x-{_platform.system().lower()}-header: common-code-python"
)

_CLI_PREFIX = """You are Common Code, an AI programming assistant - the official CLI for Common. \
You help users with software engineering tasks using the tools available to you."""

_STATIC_SECTIONS = """# Core Behavior
- Follow user instructions precisely. Use tools to complete tasks rather than guessing.
- When uncertain, ask the user for clarification rather than making assumptions.
- Read and understand existing code before suggesting modifications.
- Prefer editing existing files over creating new ones to prevent file bloat.
- If an approach fails, diagnose why before switching tactics. Do not retry the identical action blindly.

# Tool Usage
- Use dedicated tools (Read, Edit, Write, Glob, Grep) instead of shell commands when available.
- Call multiple independent tools in parallel for efficiency.
- Verify tool results rather than assuming they succeeded.
- Do not re-attempt a tool call that the user has denied - adjust your approach instead.

# Safety Rules
- Never execute destructive operations (rm -rf, force push, drop tables) without explicit user confirmation.
- Never expose or log secrets, API keys, or credentials.
- Be careful not to introduce security vulnerabilities (command injection, XSS, SQL injection, etc.).
- Protect sensitive files (.env, credentials) - never commit them.
- Only take risky actions carefully; when in doubt, ask before acting."""


# Skill 使用指导段（当有可用 skill 时注入）
_SKILL_GUIDANCE = """\
# Skill Usage
- Skills are available via the Skill tool. A listing of available skills is provided in the conversation.
- Each skill has a name, description, and when_to_use field. Use these to determine if a skill matches the user's request.
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response.
- Skills can also be triggered by the user via /skill-name. When the user types a slash command that matches a skill, expand it.
- Do not invoke a skill that is not in the listing."""


# Subagent 使用指导段（当 Agent 工具启用时注入）
_SUBAGENT_GUIDANCE = """\
# Subagent Usage
- Use the Agent tool to delegate tasks to subagents with isolated context.
- Available agent types:
  - general-purpose: Full tool access, for complex research and multi-step tasks
  - Explore: Read-only, for fast codebase search and information location
- When to use subagents:
  - Complex research tasks that need many tool calls (saves main context)
  - Independent tasks that can run in parallel
  - Read-only exploration where you don't want to clutter main context
- The subagent's result is NOT visible to the user. You must relay key findings.
- For parallel independent tasks, issue multiple Agent tool calls in a single message.
- Subagents do NOT inherit your conversation history - give them complete instructions."""


# Team 协作指导段（当团队模式启用时注入）
_TEAM_GUIDANCE = """\
# Team Collaboration
- You are the leader of a team. Use TeamCreate to create a team, then spawn teammates.
- Spawn teammates with the Agent tool using team_name + name parameters.
- Create tasks with TaskCreate and assign them to teammates with TaskUpdate (set owner).
- Communicate with teammates using SendMessage. Messages are delivered as new conversation turns.
- Broadcast with to="*" to reach all team members.
- Teammates are flat - they cannot spawn their own teammates.
- When a teammate is done, it enters idle state. Send a message to wake it for more work.
- To shut down a teammate, send a message containing "[shutdown_request]".
- Use TaskList to monitor overall progress across the team."""


# ---------------------------------------------------------------------------
# get_system_prompt_sections
# ---------------------------------------------------------------------------

def get_system_prompt_sections(
    project_info: str | None = None,
    user_instructions: str | None = None,
) -> list[SystemPromptSection]:
    """返回系统提示词段列表。

    段按顺序：
    ① 归因头（不缓存）：标识 CLI 版本信息
    ② CLI 前缀（静态缓存）：CLI 工具说明
    ③ 静态 sections（静态缓存）：核心行为规则、工具使用规范、安全规则
    ④ Skill 使用指导（不缓存）：当有可用 skill 时注入
    ⑤ 动态 sections（不缓存）：当前项目信息、用户自定义指令
    """
    sections: list[SystemPromptSection] = [
        # ① 归因头
        SystemPromptSection(
            content=_ATTRIBUTION_HEADER,
            cache_scope=None,
            name="attribution_header",
        ),
        # ② CLI 前缀
        SystemPromptSection(
            content=_CLI_PREFIX,
            cache_scope="static",
            name="cli_prefix",
        ),
        # ③ 静态 sections
        SystemPromptSection(
            content=_STATIC_SECTIONS,
            cache_scope="static",
            name="static_sections",
        ),
    ]

    # ④ Skill 使用指导（当有可用 skill 时）
    try:
        from tools.skills.bundled import get_model_invocable_skills
        skills = get_model_invocable_skills()
        if skills:
            sections.append(
                SystemPromptSection(
                    content=_SKILL_GUIDANCE,
                    cache_scope=None,
                    name="skill_guidance",
                )
            )
    except ImportError:
        pass

    # ⑤ Subagent 使用指导（当 Agent 工具启用时）
    try:
        from tools.subagent.built_in_agents import get_built_in_agents
        agents = get_built_in_agents()
        if agents:
            sections.append(
                SystemPromptSection(
                    content=_SUBAGENT_GUIDANCE,
                    cache_scope=None,
                    name="subagent_guidance",
                )
            )
    except ImportError:
        pass

    # ⑥ Team 协作指导（当团队模式启用时）
    try:
        from tools.team.manager import get_current_team
        if get_current_team() is not None:
            sections.append(
                SystemPromptSection(
                    content=_TEAM_GUIDANCE,
                    cache_scope=None,
                    name="team_guidance",
                )
            )
    except ImportError:
        pass

    # ⑦ 动态 sections
    if project_info is not None:
        sections.append(
            SystemPromptSection(
                content=project_info,
                cache_scope=None,
                name="project_info",
            )
        )

    if user_instructions is not None:
        sections.append(
            SystemPromptSection(
                content=user_instructions,
                cache_scope=None,
                name="user_instructions",
            )
        )

    return sections
