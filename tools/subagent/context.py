"""子代理上下文工厂。

为子代理创建隔离的执行上下文：克隆文件状态缓存、隔离消息历史、
共享模型客户端，设置 agent_id 和深度计数。

默认所有可变状态都隔离，仅显式 opt-in 共享特定回调。
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from tools.protocol import ToolUseContext
from tools.subagent.types import AgentDefinition


# ---------------------------------------------------------------------------
# SubagentContext — 子代理执行上下文
# ---------------------------------------------------------------------------


@dataclass
class SubagentContext:
    """子代理执行上下文，持有子代理执行所需的全部隔离状态。

    Attributes:
        agent_id: 子代理唯一标识
        depth: 嵌套深度（主代理=0，子代理=1）
        agent_def: 代理类型定义
        tool_use_context: 隔离的工具执行上下文
        model: 解析后的模型名
        max_turns: 最大循环轮数
        token_budget: 累计 token 预算（0/None 表示不限）
        is_async: 是否异步执行（后台运行）
        initial_messages: 子代理的初始消息列表（仅含 prompt）
        abort_event: 中断事件——同步子代理共享父的，异步子代理独立
        pending_messages: 待投递的消息队列（SendMessage 续接时入队）
        on_activity: 活动上报回调（活性看门狗用，每条消息调用一次）
        stop_reason: 显式停止原因（看门狗超时/主动停止），空串表示非显式停止
    """

    agent_id: str
    depth: int
    agent_def: AgentDefinition
    tool_use_context: ToolUseContext
    model: str
    max_turns: int | None = None
    token_budget: int | None = None
    is_async: bool = False
    initial_messages: list[dict] = field(default_factory=list)
    abort_event: asyncio.Event | None = None
    pending_messages: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    # 子会话 id（会话化绑定结果；空串表示无子会话的降级模式）
    child_session_id: str = ""
    on_activity: Any = None
    stop_reason: str = ""


# ---------------------------------------------------------------------------
# build_subagent_system_prompt — 系统提示词组装（Notes / env / AGENTS.md 注入）
# ---------------------------------------------------------------------------


# 所有子代理共享的注意事项（内置与自定义 .md 代理统一生效）
_SUBAGENT_NOTES = """\
Notes:
- Agent threads always have their cwd reset between bash calls, as a result please only use absolute file paths.
- In your final response, share file paths (always absolute, never relative) that are relevant to the task. Include code snippets only when the exact text is load-bearing (e.g., a bug you found, a function signature the caller asked for) — do not recap code you merely read.
- For clear communication with the user the assistant MUST avoid using emojis.
- Do not use a colon before tool calls.
- Do NOT Write report/summary/findings/analysis .md files. Return findings directly as your final assistant message — the parent agent reads your text output, not files you create."""


def build_subagent_system_prompt(agent_def: AgentDefinition, model: str | None = None) -> str:
    """组装子代理系统提示词：定义提示词 + 共享 Notes + 环境块 + AGENTS.md（按开关注入）。

    拼接顺序静态在前、动态在后（代理提示词与 Notes 恒定，env 随工作区与模型变化），
    利于请求侧前缀缓存；AGENTS.md 读取失败或文件不存在时静默跳过，不阻断派生。
    """
    parts = [
        agent_def.resolve_system_prompt().rstrip(),
        _SUBAGENT_NOTES,
        _build_env_block(model),
    ]
    base = "\n\n".join(p for p in parts if p)
    if not agent_def.inject_agents_md:
        return base
    agents_md = _read_workspace_agents_md()
    if not agents_md:
        return base
    return f"{base}\n\n# 工作区规范（AGENTS.md）\n\n{agents_md}"


def _build_env_block(model: str | None) -> str:
    """构造子代理环境信息块：工作区事实 + 模型名（动态段，位于提示词末段）。"""
    import platform as _platform
    from pathlib import Path

    lines: list[str] = []
    root = ""
    try:
        from server.paths import effective_root

        root = effective_root()
    except Exception:
        pass  # 工作区指针未就绪等场景跳过路径事实
    if root:
        lines.append(f"Working directory: {root}")
        git_dir = Path(root) / ".git"
        is_repo = git_dir.exists()
        lines.append(f"Is directory a git repo: {'Yes' if is_repo else 'No'}")
        if is_repo:
            branch = _read_git_branch(git_dir)
            if branch:
                lines.append(f"Git branch: {branch}")
    lines.append(f"Platform: {_platform.system().lower()}")
    lines.append(f"OS Version: {_platform.version()}")
    lines.append("Shell: Git Bash")
    block = "<env>\n" + "\n".join(lines) + "\n</env>"
    if model:
        block += f"\n\nYou are powered by the model named {model}."
    return block


def _read_git_branch(git_dir) -> str:
    """从 .git/HEAD 解析当前分支名；worktree/分离头等形态失败返回空串。

    直接读文件而非 subprocess，避免与 query.loop 的分支缓存互相导入。
    """
    from pathlib import Path

    try:
        head = (Path(git_dir) / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):].strip()
    # 分离头形态给短哈希前缀
    return head[:12] if head else ""


def _read_workspace_agents_md() -> str:
    """读取工作区根目录的 AGENTS.md 内容，失败返回空串。"""
    try:
        from pathlib import Path

        from server.paths import effective_root

        path = Path(effective_root()) / "AGENTS.md"
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        pass  # 工作区指针未就绪等场景静默跳过
    return ""


# ---------------------------------------------------------------------------
# create_subagent_context — 创建隔离的子代理上下文
# ---------------------------------------------------------------------------


def create_subagent_context(
    parent_context: ToolUseContext | None,
    agent_def: AgentDefinition,
    main_loop_model: str,
    *,
    agent_id: str | None = None,
    depth: int = 1,
    is_async: bool = False,
    prompt: str = "",
    parent_abort_event: asyncio.Event | None = None,
) -> SubagentContext:
    """从父上下文创建隔离的子代理上下文。

    隔离策略：
    - messages: 全新列表（仅含 prompt 转换的 user 消息），不继承父对话
    - file_state_cache: 克隆父的缓存（子代理可独立修改不影响父）
    - abort_event: 同步子代理共享父的（parent_abort_event），异步子代理创建独立的
    - model: 按 agent_def 解析（inherit 则用主循环模型）
    - max_turns: 从 agent_def 取

    Args:
        parent_context: 父代理的 ToolUseContext，None 表示无父上下文
        agent_def: 代理类型定义
        main_loop_model: 主循环模型名
        agent_id: 指定 agent_id，None 则自动生成
        depth: 嵌套深度
        is_async: 是否异步执行
        prompt: 子代理的任务指令
        parent_abort_event: 父代理的中断事件，同步子代理共享此事件

    Returns:
        SubagentContext 隔离上下文
    """
    # 生成 agent_id
    resolved_agent_id = agent_id or f"agent_{uuid.uuid4().hex[:8]}"

    # 解析模型
    model = agent_def.resolve_model(main_loop_model)

    # 克隆文件状态缓存（隔离可变状态）
    if parent_context is not None:
        file_state_cache = copy.deepcopy(parent_context.file_state_cache)
    else:
        file_state_cache = {}

    # 创建隔离的 ToolUseContext
    tool_use_context = ToolUseContext(
        permission_decision=None,
        messages=[],  # 全新消息列表
        file_state_cache=file_state_cache,
        abort_controller=None,  # 全新中断控制
        tool_use_id=resolved_agent_id,
    )

    # abort 事件：同步共享父的，异步创建独立的
    if is_async:
        abort_event = asyncio.Event()
    else:
        abort_event = parent_abort_event  # 同步子代理共享父的中断信号

    # 构建初始消息（prompt 作为首轮 user 消息）
    initial_messages: list[dict] = []
    if prompt:
        initial_messages.append({"role": "user", "content": prompt})

    return SubagentContext(
        agent_id=resolved_agent_id,
        depth=depth,
        agent_def=agent_def,
        tool_use_context=tool_use_context,
        model=model,
        max_turns=agent_def.max_turns,
        is_async=is_async,
        initial_messages=initial_messages,
        abort_event=abort_event,
    )
