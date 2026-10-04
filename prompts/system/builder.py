"""系统提示词段组装。"""

from __future__ import annotations

from prompts.system.sections import (
    SystemPromptSection,
    _ATTRIBUTION_HEADER,
    _CLI_PREFIX,
    build_skill_guidance,
    build_static_sections,
    build_subagent_guidance,
    build_team_guidance,
)


def get_system_prompt_sections(
    project_info: str | None = None,
    user_instructions: str | None = None,
) -> list[SystemPromptSection]:
    """返回系统提示词段列表。

    段按顺序：
    ① 归因头（不缓存）：标识 CLI 版本信息
    ② CLI 前缀（静态缓存）：CLI 工具说明
    ③ 静态 sections（静态缓存）：.j2 模板按序拼接——核心行为、工具使用策略、
       最终回复、交付、安全规则、文件引用、任务规划
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
            content=build_static_sections(),
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
                    content=build_skill_guidance(),
                    cache_scope=None,
                    name="skill_guidance",
                )
            )
    except ImportError:
        pass

    # ⑤ Subagent 使用指导（动态：代理清单含自定义 .md 代理，加载后自动出现在提示词）
    try:
        from tools.subagent.built_in_agents import get_built_in_agents
        agents = get_built_in_agents()
        if agents:
            sections.append(
                SystemPromptSection(
                    content=build_subagent_guidance(),
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
                    content=build_team_guidance(),
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
