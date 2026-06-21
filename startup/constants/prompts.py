"""系统提示词构建。"""

from __future__ import annotations

import platform as _platform
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


# ---------------------------------------------------------------------------
# SystemPromptSection — 系统提示段落
# ---------------------------------------------------------------------------

@dataclass
class SystemPromptSection:
    """系统提示词段。"""

    content: str
    cache_scope: str | None  # "global" / "static" / None
    name: str


# ---------------------------------------------------------------------------
# 核心提示词内容（简化版，保留关键规则）
# ---------------------------------------------------------------------------

import platform as _platform

# 归因头：标识客户端类型和运行平台
_ATTRIBUTION_HEADER = (
    f"x-{_platform.system().lower()}-header: common-code-python"
)

_CLI_PREFIX = """You are Common Code, an AI programming assistant — the official CLI for Common. \
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
- Do not re-attempt a tool call that the user has denied — adjust your approach instead.

# Safety Rules
- Never execute destructive operations (rm -rf, force push, drop tables) without explicit user confirmation.
- Never expose or log secrets, API keys, or credentials.
- Be careful not to introduce security vulnerabilities (command injection, XSS, SQL injection, etc.).
- Protect sensitive files (.env, credentials) — never commit them.
- Only take risky actions carefully; when in doubt, ask before acting."""


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
    ④ 动态 sections（不缓存）：当前项目信息、用户自定义指令
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

    # ④ 动态 sections
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


# ---------------------------------------------------------------------------
# build_system_messages
# ---------------------------------------------------------------------------

def build_system_messages(sections: list[SystemPromptSection]) -> list[dict]:
    """将段列表转换为 OpenAI system 消息列表。

    - 静态段（cache_scope 为 "static" 或 "global"）合并为一条 system 消息放在最前
    - 动态段（cache_scope 为 None）作为单独 system 消息追加
    """
    static_parts: list[str] = []
    dynamic_messages: list[dict] = []

    for section in sections:
        if section.cache_scope in ("static", "global"):
            static_parts.append(section.content)
        else:
            dynamic_messages.append({"role": "system", "content": section.content})

    messages: list[dict] = []
    if static_parts:
        messages.append({"role": "system", "content": "\n\n".join(static_parts)})

    messages.extend(dynamic_messages)
    return messages
