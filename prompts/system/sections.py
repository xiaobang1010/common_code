"""系统提示词段结构与组装。

段的正文全部来自 prompts/templates/system/ 下的 .j2 模板（见 prompts/loader.py），
本模块只负责：定义段顺序、渲染、拼接静态段、给动态段注入变量。
"""

from __future__ import annotations

import platform as _platform
from dataclasses import dataclass

from prompts.loader import render_prompt

# 静态段：内容不随会话变化，整块走静态缓存；顺序即拼接顺序。
# 正文除标注「我方机制」外均为参考模板原文（原文照抄，仅品牌与机制冲突处调整），
# 原文留档见 context/ref/ 对应目录。
_SYSTEM_STATIC_SECTION_NAMES = [
    "opening",
    "content-policy",
    "personal-files-safety",
    "windows-command-safety",  # 仅 Windows 平台拼入
    "regional-conventions",
    "agent-loop",
    "result-presentation",
    "sharing-files",
    "final-answer-instructions",
    "tool-use",
    "visualizer",  # 可视化交付：widget_guidelines + show_widget（沙箱渲染已实装）
    "task-planning",  # 我方机制：spec 三件套 / TodoWrite 轻清单
    "asking-questions",
    "tool-usage-policy",
    "doing-tasks",
    "tone-and-style",
    "output-efficiency",
    "executing-actions-with-care",
    "security-boundaries",
    "code-references",
    "file-references",  # 我方机制：消息内文件引用
    "agent-skills",
]


@dataclass
class SystemPromptSection:
    """系统提示词段。"""

    content: str
    cache_scope: str | None  # "global" / "static" / None
    name: str


# 归因头：标识客户端类型和运行平台
_ATTRIBUTION_HEADER = (
    f"x-{_platform.system().lower()}-header: common-code-python"
)

_CLI_PREFIX = """You are Common Code, an AI programming assistant - the official CLI for Common. \
You help users with software engineering tasks using the tools available to you."""


def build_static_sections() -> str:
    """按序拼接全部静态段模板，段间以一个空行分隔。

    windows-command-safety 仅在 Windows 平台拼入（内容为其专用安全规约）。
    """
    names = [
        n
        for n in _SYSTEM_STATIC_SECTION_NAMES
        if n != "windows-command-safety" or _platform.system() == "Windows"
    ]
    parts = [render_prompt(f"system/{n}.j2").rstrip() for n in names]
    return "\n\n".join(parts)


def build_skill_guidance() -> str:
    """Skill 使用指导。"""
    return render_prompt("system/skill-guidance.j2")


def build_team_guidance() -> str:
    """Team 协作指导。"""
    return render_prompt("system/team-guidance.j2")


def build_subagent_guidance() -> str:
    """构建子代理使用指导（代理清单来自 get_agent_listing，含自定义代理）。

    清单取数失败时退化为内置两类，保证提示词构建必须容错。
    """
    try:
        from tools.subagent.built_in_agents import get_agent_listing

        listing = get_agent_listing()
    except Exception:  # noqa: BLE001 提示词构建必须容错
        listing = []

    if not listing:
        listing = [
            {"type": "general-purpose", "when_to_use": "通用研究与多步骤任务"},
            {"type": "Explore", "when_to_use": "只读探索，快速定位代码库信息"},
        ]
    return render_prompt("system/subagent-guidance.j2", agents=listing)
