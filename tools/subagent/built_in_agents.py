"""内置子代理类型定义。

注册两种内置代理：
- general-purpose：全工具，通用研究/搜索/多步骤任务
- Explore：只读检索，六工具白名单（Bash/Glob/Grep/Read/WebFetch/TodoWrite），
  Bash 仅放行只读命令（权限链路判定）

自定义代理（.md 文件加载）在后续阶段实现。
"""

from __future__ import annotations

from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# general-purpose 代理系统提示词
# ---------------------------------------------------------------------------

_GENERAL_PURPOSE_PROMPT = """\
You are an agent for Common Code CLI. Given the user's message, you should use the tools available to complete the task. Complete the task fully—don't gold-plate, but don't leave it half-done. When you complete the task, respond with a concise report covering what was done and any key findings — the caller will relay this to the user, so it only needs the essentials.

Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives. Use Read when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions, look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested.
"""


# ---------------------------------------------------------------------------
# Explore 代理系统提示词
# ---------------------------------------------------------------------------

_EXPLORE_PROMPT = """\
You are Common Code Explore, a file search and codebase research specialist for Common Code CLI. You excel at thoroughly navigating and exploring codebases.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED FROM:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code. You do NOT have access to file editing tools - attempting to edit file editing tools will fail.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use Glob for broad file pattern matching
- Use Grep for searching file contents with regex
- Use Read when you know the specific file path you need to read
- Use Bash ONLY for read-only operations (ls, git status, git log, git diff, find, grep, cat, head, tail)
- NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, or any file creation/modification
- Adapt your search approach based on the thoroughness level specified by the caller
- Communicate your final report directly as a regular message - do NOT attempt to create files

NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:
- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations
- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files

Complete the user's search request efficiently and report your findings clearly.
"""


# ---------------------------------------------------------------------------
# 所有子代理默认禁用的工具（防止递归等问题）
# ---------------------------------------------------------------------------

# Agent 工具对所有子代理禁用，防止无限递归
ALL_AGENT_DISALLOWED_TOOLS: list[str] = ["Agent"]


# ---------------------------------------------------------------------------
# 内置代理定义
# ---------------------------------------------------------------------------


def _general_purpose_agent() -> AgentDefinition:
    """general-purpose 代理：全工具，通用任务。"""
    return AgentDefinition(
        agent_type="general-purpose",
        when_to_use=(
            "General-purpose agent for researching complex questions, searching for "
            "code, and executing multi-step tasks. When you are searching for a "
            "keyword or file and are not confident that you will find the right "
            "match in the first few tries use this agent to perform the search "
            "for you."
        ),
        tools=None,  # None = 全部工具
        disallowed_tools=list(ALL_AGENT_DISALLOWED_TOOLS),
        system_prompt=_GENERAL_PURPOSE_PROMPT,
        source="built-in",
    )


def _explore_agent() -> AgentDefinition:
    """Explore 代理：只读检索，六工具白名单（Bash 仅只读命令，权限链路按白名单放行）。"""
    return AgentDefinition(
        agent_type="Explore",
        when_to_use=(
            "Read-only search agent for broad fan-out searches - when answering "
            "means sweeping many files, directories, or naming conventions and "
            "need only the conclusion, not the file dumps. It reads excerpts "
            "rather than whole files, so it locates code; it doesn't review or "
            "audit it. Specify search breadth: \"medium\" for moderate "
            "exploration, \"very thorough\" for multiple locations and naming "
            "conventions."
        ),
        # 白名单而非通配减黑名单：只给快搜需要的工具（WebSearch 工具尚不存在，暂缺）
        tools=["Bash", "Glob", "Grep", "Read", "WebFetch", "TodoWrite"],
        disallowed_tools=list(ALL_AGENT_DISALLOWED_TOOLS),
        model="inherit",
        system_prompt=_EXPLORE_PROMPT,
        # Explore 是快速搜索代理，不注入工作区规范（保持轻快）
        inject_agents_md=False,
        source="built-in",
    )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def get_built_in_agents() -> list[AgentDefinition]:
    """获取所有内置代理定义。"""
    return [
        _general_purpose_agent(),
        _explore_agent(),
    ]


def find_agent_by_type(agent_type: str) -> AgentDefinition | None:
    """按 agent_type 查找代理定义（精确或唯一模糊命中）。

    查找顺序：内置代理 -> 插件代理 -> 自定义代理（.md 加载，用户级覆盖项目级）。
    需要歧义/未命中结构化信息的调用方请直接用 resolver.resolve_agent_type。
    """
    from tools.subagent.resolver import resolve_agent_type

    result = resolve_agent_type(agent_type)
    return result.agent if result.kind == "matched" else None


def get_agent_listing() -> list[dict[str, str]]:
    """获取代理类型列表（内置 + 插件 + 自定义），用于注入系统提示词。

    返回 [{"type": ..., "when_to_use": ..., "tools": ...}, ...]
    """
    from tools.subagent.loader import load_custom_agents
    from tools.subagent.plugin_agents import get_plugin_agent_definitions

    listing: list[dict[str, str]] = []
    custom_agents, _ = load_custom_agents()
    plugin_agents = get_plugin_agent_definitions()
    seen: set[str] = set()
    for agent in get_built_in_agents() + plugin_agents + custom_agents:
        if agent.agent_type in seen:
            continue  # 内置优先，跳过同名代理
        seen.add(agent.agent_type)
        tools_desc = "all" if agent.has_wildcard_tools() else ", ".join(agent.tools or [])
        if agent.disallowed_tools:
            tools_desc += f" (disallowed: {', '.join(agent.disallowed_tools)})"
        listing.append({
            "type": agent.agent_type,
            "when_to_use": agent.when_to_use,
            "tools": tools_desc,
        })
    return listing
