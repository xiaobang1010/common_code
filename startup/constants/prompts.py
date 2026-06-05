"""系统提示词构建 — 参考原始 src/constants/prompts.ts。"""

from __future__ import annotations

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

_ATTRIBUTION_HEADER = (
    "x-anthropic-billing-header: common-code-python"
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


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from query.utils.system_prompt import SystemPromptRegistry, registry
    from query.utils.api import build_api_request

    # ---- 1. 测试 get_system_prompt_sections() ----
    sections = get_system_prompt_sections(
        project_info="# Environment\nWorking directory: /tmp/project",
        user_instructions="# Custom\nAlways respond in Chinese.",
    )
    assert len(sections) == 5, f"Expected 5 sections, got {len(sections)}"
    assert sections[0].name == "attribution_header"
    assert sections[0].cache_scope is None
    assert sections[1].name == "cli_prefix"
    assert sections[1].cache_scope == "static"
    assert sections[2].name == "static_sections"
    assert sections[2].cache_scope == "static"
    assert sections[3].name == "project_info"
    assert sections[3].cache_scope is None
    assert sections[4].name == "user_instructions"
    assert sections[4].cache_scope is None
    print("[PASS] get_system_prompt_sections() 返回段列表")

    # ---- 2. 测试 build_system_messages() 静态/动态分段 ----
    messages = build_system_messages(sections)
    # 2 个静态段合并为 1 条 + 3 个动态段各 1 条 = 4 条
    assert len(messages) == 4, f"Expected 4 messages, got {len(messages)}"
    # 第一条是静态合并
    assert messages[0]["role"] == "system"
    assert _CLI_PREFIX in messages[0]["content"]
    assert _STATIC_SECTIONS in messages[0]["content"]
    # attribution_header 是第一条动态
    assert messages[1]["role"] == "system"
    assert messages[1]["content"] == _ATTRIBUTION_HEADER
    # project_info 是第二条动态
    assert messages[2]["role"] == "system"
    assert "Working directory" in messages[2]["content"]
    # user_instructions 是第三条动态
    assert messages[3]["role"] == "system"
    assert "Chinese" in messages[3]["content"]
    print("[PASS] build_system_messages() 静态/动态分段")

    # ---- 3. 测试无额外动态段时 ----
    only_static = get_system_prompt_sections()
    # 3 段：attribution_header(None) + cli_prefix(static) + static_sections(static)
    # = 1 条静态合并 + 1 条动态 = 2 条消息
    msgs = build_system_messages(only_static)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "system"
    print("[PASS] build_system_messages() 无额外动态段")

    # ---- 4. 测试 SystemPromptRegistry 注册/取消 ----
    reg = SystemPromptRegistry()
    reg.register("test_section", "Hello from test", cache_scope="static")
    reg.register("another", "Dynamic content")
    secs = reg.get_sections()
    assert len(secs) == 2
    assert secs[0].name == "test_section"
    assert secs[0].cache_scope == "static"
    assert secs[1].name == "another"
    assert secs[1].cache_scope is None

    reg.unregister("test_section")
    secs = reg.get_sections()
    assert len(secs) == 1
    assert secs[0].name == "another"
    print("[PASS] SystemPromptRegistry 注册/取消")

    # ---- 5. 测试全局 registry 单例便捷函数 ----
    from query.utils.system_prompt import register_dynamic_section, unregister_dynamic_section
    register_dynamic_section("global_test", "Global test content")
    assert any(s.name == "global_test" for s in registry.get_sections())
    unregister_dynamic_section("global_test")
    assert not any(s.name == "global_test" for s in registry.get_sections())
    print("[PASS] 全局 registry 便捷函数")

    # ---- 6. 测试 build_api_request() 请求体构建 ----
    req = build_api_request(
        messages=[{"role": "user", "content": "Hello"}],
        system_prompt=messages,
        tools=[],
        model="gpt-4",
        stream=True,
        max_tokens=4096,
    )
    assert req["model"] == "gpt-4"
    assert req["stream"] is True
    assert req["max_tokens"] == 4096
    # system 消息应排在 messages 前面
    all_msgs = req["messages"]
    assert all_msgs[0]["role"] == "system"
    assert all_msgs[-1]["role"] == "user"
    print("[PASS] build_api_request() 请求体构建")

    print("\nAll tests passed!")
