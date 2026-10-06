"""查询引擎 — 会话级状态持有者。

查询引擎 — 会话级状态持有者。

QueryEngine 持有会话状态（消息历史、token 用量、轮次），
跨多次 submitMessage 持久化。QueryEngineConfig 是会话级不可变配置。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

from query.config import build_query_config
from query.deps import QueryDeps, production_deps
from tools import get_tools


# ---------------------------------------------------------------------------
# QueryEngineConfig — 会话级配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryEngineConfig:
    """会话级配置，构造时确定，整个会话期间不变。

    Attributes:
        cwd: 工作目录
        model: 模型名称
        max_tokens: 最大输出 token 数
        temperature: 采样温度
        reasoning_level: 本会话所选推理等级（空串=跟随模型默认，不注入推理参数）
        permission_mode: 权限模式
        tools: 可用工具列表
        system_prompt_sections: 系统提示词段落
        max_turns: 最大轮次（None 表示不限）
        token_budget: 累计 token 预算（None 或 0 表示不限）；超限当轮优雅停止
        permission_check: 工具调用前的权限检查回调，None 表示跳过权限检查
        permission_prompt: 权限确认弹窗回调，当 permission_check 返回 ask 决策时调用。
            签名 (tool_name, tool_input, reason) -> "allow"|"deny"|"always_allow"，None 表示无弹窗
        question_prompt: AskUserQuestion 提问回调，模型主动提问时调用。
            签名 async (question, options) -> 用户回答文本，None 表示无提问通道
        abort_event: 会话级中断事件。/api/abort 置位后经 ToolUseContext.abort_controller
            传导到前台子代理触发优雅退出；None 表示无中断通道
        deps: I/O 依赖
    """

    cwd: str = ""
    model: str = ""  # 空字符串表示用 get_default_model() 解析，避免硬编码错误模型
    max_tokens: int = 8192
    temperature: float = 1.0
    # 本会话所选推理等级（空串=跟随模型默认，不注入任何推理参数）
    reasoning_level: str = ""
    permission_mode: str = "default"
    tools: list[Any] = field(default_factory=get_tools)
    system_prompt_sections: list[Any] = field(default_factory=list)
    max_turns: int | None = None
    token_budget: int | None = None
    permission_check: Callable | None = None
    permission_prompt: Callable | None = None
    question_prompt: Callable | None = None
    abort_event: asyncio.Event | None = None
    deps: QueryDeps = field(default_factory=production_deps)


# ---------------------------------------------------------------------------
# Explore 子代理 Bash 只读白名单（spec 附录 F）
# ---------------------------------------------------------------------------

# 直接放行的首命令词
_EXPLORE_BASH_READONLY_COMMANDS = frozenset({
    "ls", "pwd", "cat", "head", "tail", "find", "grep", "rg", "echo",
    "wc", "which", "where", "file", "stat", "du", "df", "sort", "uniq", "diff",
})
# git 仅放行的只读二级子命令
_EXPLORE_BASH_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "blame", "rev-parse",
    "ls-files", "describe", "remote",
})
# remote 仅允许查看形态（无参列出 / -v）
_GIT_REMOTE_VIEW_ARGS = frozenset({"", "-v", "--verbose"})
# 出现即视为白名单外：重定向/管道/命令替换/heredoc 元字符与 tee
_EXPLORE_BASH_FORBIDDEN_CHARS = ("<<", ">>", ">", "|", "`", "$(")
_EXPLORE_BASH_FORBIDDEN_WORDS = {"tee"}

_EXPLORE_BASH_DENY_REASON = (
    "Permission required but subagents cannot ask the user for approval, "
    "so this tool call was denied. Explore subagents can only run "
    "read-only commands (ls, cat, head, tail, find, grep, rg, git "
    "status/log/diff/show, etc.). If this action needs approval, run it "
    "in the main conversation instead."
)


def _explore_bash_decision(tool, input_args, context) -> dict | None:
    """Explore 子代理 Bash 只读判定：命中白名单返回 allow，未命中返回 deny。

    仅作用于「Explore 类型子代理的 Bash 调用」这一场景，其余（非 Bash、
    非子代理上下文、非 Explore）返回 None 交由通用权限链路，行为不变。
    子代理无审批通道，通用链路对非删除命令默认放行对只读代理过宽，
    故在本检查点独立产出 allow/deny。
    """
    if getattr(tool, "name", "") != "Bash":
        return None
    from tools.subagent.tools import is_subagent_context

    if not is_subagent_context(context):
        return None
    agent_id = getattr(context, "tool_use_id", "")
    try:
        from tools.subagent.registry import get_subagent_registry

        task = get_subagent_registry().get(agent_id)
    except Exception:
        task = None
    if task is None or getattr(task, "agent_type", "") != "Explore":
        return None

    command = getattr(input_args, "command", None)
    if not isinstance(command, str) or not command.strip():
        return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}
    if any(ch in command for ch in _EXPLORE_BASH_FORBIDDEN_CHARS):
        return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}

    # 复合命令（;、&&、||）逐段校验，任一段越界即整体拒绝
    import re
    import shlex

    for segment in re.split(r";|&&|\|\|", command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}
        if not tokens:
            continue
        if any(t in _EXPLORE_BASH_FORBIDDEN_WORDS for t in tokens):
            return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}
        # 取首词的裸命令名（容忍绝对路径与 .exe 后缀形态）
        head = tokens[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if head.endswith(".exe"):
            head = head[:-4]
        if head == "git":
            sub = tokens[1] if len(tokens) > 1 else ""
            if sub not in _EXPLORE_BASH_GIT_SUBCOMMANDS:
                return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}
            if sub == "remote" and any(
                arg not in _GIT_REMOTE_VIEW_ARGS for arg in tokens[2:]
            ):
                return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}
        elif head not in _EXPLORE_BASH_READONLY_COMMANDS:
            return {"decision": "deny", "reason": _EXPLORE_BASH_DENY_REASON}
    return {"decision": "allow"}


# ---------------------------------------------------------------------------
# _default_permission_check — 默认权限检查
# ---------------------------------------------------------------------------


async def _default_permission_check(tool, input_args, context):
    """默认权限检查 — 调用 has_permissions_to_use_tool。

    从 bootstrap state 读取 permission_mode 和权限规则，构建 context 传入。
    返回值：
        {"decision": "allow"} — 允许执行
        {"decision": "deny", "reason": ...} — 拒绝
        {"decision": "ask", "reason": ...} — 需要用户确认，由上层调弹窗回调处理
    """
    # Explore 子代理的 Bash 只读白名单判定（优先于通用规则）
    explore_bash = _explore_bash_decision(tool, input_args, context)
    if explore_bash is not None:
        return explore_bash

    from tools.utils.permissions.permissions import has_permissions_to_use_tool
    from startup.bootstrap.state import get_permission_mode

    # 从合并后的设置读取权限规则
    perm_context: dict = {
        "permission_mode": get_permission_mode(),
    }
    try:
        from startup.config import get_initial_settings
        settings = get_initial_settings()
        perms = settings.permissions
        perm_context["deny_rules"] = perms.deny
        perm_context["ask_rules"] = perms.ask
        perm_context["allow_rules"] = perms.allow
    except Exception:
        # 设置系统未初始化时用空规则（仅靠模式分流）
        pass

    result = has_permissions_to_use_tool(
        tool_name=tool.name,
        tool_input=input_args.model_dump() if hasattr(input_args, "model_dump") else input_args,
        context=perm_context,
        tool=tool,
    )
    if result.decision.value == "deny":
        return {"decision": "deny", "reason": result.reason}
    if result.decision.value == "ask":
        # ASK 不再静默放行，透传给上层（executor 调 permission_prompt 弹窗）
        return {"decision": "ask", "reason": result.reason}
    return {"decision": "allow"}


# ---------------------------------------------------------------------------
# build_engine_config — 工厂函数
# ---------------------------------------------------------------------------


def build_engine_config(**overrides: Any) -> QueryEngineConfig:
    """构建会话级配置，从环境变量读默认值。

    环境变量映射：
      - COMMON_CODE_MODEL → model（兼容旧变量，优先用 get_default_model 统一配置路径）
      - COMMON_CODE_MAX_TOKENS → max_tokens（默认 8192）
      - COMMON_CODE_TEMPERATURE → temperature（默认 1.0）
      - COMMON_CODE_PERMISSION_MODE → permission_mode（默认 "default"）

    model 字段优先级：COMMON_CODE_MODEL 环境变量 > get_default_model()（走 LLM_MODEL/配置文件/默认值）
    这样 .env 里的 LLM_MODEL 和 ~/.agent/config.json 里的 llm_model 都能生效。

    permission_check 字段单独处理：
      - 调用方显式传了 permission_check（包括 None）→ 用传入值
      - 调用方未传 → 用 _default_permission_check

    其余字段（cwd、system_prompt_sections、max_turns、deps）
    使用 dataclass 默认值。tools 默认调 get_tools() 装好内置 6 个工具。

    Args:
        **overrides: 覆盖字段值

    Returns:
        QueryEngineConfig 不可变会话级配置
    """
    # model 优先用 COMMON_CODE_MODEL 环境变量，没有就走统一的 get_default_model()
    from query.services.api.client import get_default_model
    default_model = os.environ.get("COMMON_CODE_MODEL") or get_default_model()

    defaults: dict[str, Any] = {
        "model": default_model,
        "max_tokens": int(os.environ.get("COMMON_CODE_MAX_TOKENS", "8192")),
        "temperature": float(os.environ.get("COMMON_CODE_TEMPERATURE", "1.0")),
        "permission_mode": os.environ.get("COMMON_CODE_PERMISSION_MODE", "default"),
    }
    # permission_check 不放 defaults，单独处理：尊重显式传入的 None，未传则用默认函数
    if "permission_check" in overrides:
        defaults["permission_check"] = overrides["permission_check"]
    else:
        defaults["permission_check"] = _default_permission_check
    defaults.update({k: v for k, v in overrides.items() if k != "permission_check"})
    return QueryEngineConfig(**defaults)


# ---------------------------------------------------------------------------
# QueryEngine — 有状态引擎
# ---------------------------------------------------------------------------


class QueryEngine:
    """有状态引擎，持有会话状态，跨多次 submitMessage 持久化。

    持有的会话状态包括：
      - mutable_messages: 可变消息列表，每轮迭代读写
      - total_usage: 累计 token 使用量
      - turn_count: 轮次计数（每次 submitMessage 结束 +1）

    不可变配置通过 config 属性暴露，I/O 依赖通过 deps 属性暴露。
    """

    def __init__(
        self,
        config: QueryEngineConfig,
        initial_messages: list[dict] | None = None,
        session_id: str = "",
    ) -> None:
        self._config = config
        self._deps = config.deps
        self._mutable_messages: list[dict] = initial_messages or []
        self._total_usage: int = 0
        self._turn_count: int = 0
        # 整个会话一个 sessionId；调用方可显式指定（如聊天会话 id，
        # 供子代理注册表按父会话关联与通知投递），缺省生成
        self._session_id: str = session_id or config.deps.get_uuid()
        # 会话级 ALWAYS_ALLOW 集合：用户选过 always_allow 的工具后续直接放行
        self._always_allowed: set[str] = set()
        # 会话级压缩追踪：冷却基线与计数快照跨回合保留；
        # 新用户回合仅重置连败与快速再满计数（submitMessage 内执行）
        from query.services.compact.auto_compact import CompactTracking
        self._compact_tracking: CompactTracking = CompactTracking()

    @property
    def mutable_messages(self) -> list[dict]:
        return self._mutable_messages

    @mutable_messages.setter
    def mutable_messages(self, value: list[dict]) -> None:
        self._mutable_messages = value

    @property
    def total_usage(self) -> int:
        return self._total_usage

    @total_usage.setter
    def total_usage(self, value: int) -> None:
        self._total_usage = value

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def session_id(self) -> str:
        """会话 ID，整个会话不变。"""
        return self._session_id

    @property
    def always_allowed(self) -> set[str]:
        """会话级 ALWAYS_ALLOW 工具集合，跨轮持久化。"""
        return self._always_allowed

    @property
    def compact_tracking(self):
        """会话级压缩追踪状态：跨回合保留冷却基线与计数快照。"""
        return self._compact_tracking

    @property
    def config(self) -> QueryEngineConfig:
        return self._config

    @property
    def deps(self) -> QueryDeps:
        return self._deps

    @property
    def messages(self) -> list[dict]:
        """只读属性，供 REPL 渲染历史。"""
        return self._mutable_messages

    async def submitMessage(
        self,
        prompt: str | list,
        user_context: dict[str, str] | None = None,
        system_context: dict[str, str] | None = None,
        message_meta: dict | None = None,
    ) -> AsyncGenerator[Any, None]:
        """提交用户输入，启动一轮 agentic 循环。

        把 user 消息追加到 mutable_messages，构建循环级快照，
        调 query_loop，循环结束后 turn_count + 1。

        Args:
            prompt: 用户输入文本，或 OpenAI 风格 content parts 列表
                （含图时为 [{"type":"text"...},{"type":"image_url"...}]）
            user_context: 用户上下文字典
            system_context: 系统上下文字典
            message_meta: 追加进 user 消息的观测字段（如队列转正行的
                _steer/_input_id，随下划线剥离机制不外发模型）

        Yields:
            流式事件或结果消息
        """
        # 延迟 import 避免循环依赖
        from query.loop import query_loop

        # UserPromptSubmit hooks：在消息进引擎前执行，可拦截或注入上下文。
        # cwd 用 effective_root：后台任务上下文里取任务自己的工作区
        from server.paths import effective_root
        from startup.hooks import run_user_prompt_submit_hooks
        from startup.setup import get_hooks_snapshot
        from query.services.api.llm import StreamEvent
        from query.utils.messages import extract_text_from_content

        # hook 入参口径保持纯文本：parts 时拼接 text 块、图片以 [image] 占位，
        # 外部脚本的 prompt 字段形态不因多模态而变
        hook_prompt = (
            prompt if isinstance(prompt, str)
            else extract_text_from_content(prompt, image_placeholder="[image]")
        )

        hook_snapshot = get_hooks_snapshot()
        hook_result = None
        if hook_snapshot is not None:
            try:
                hook_result = await run_user_prompt_submit_hooks(
                    hook_snapshot,
                    hook_prompt,
                    self._session_id,
                    effective_root(),
                )
            except Exception:
                hook_result = None

        # hook 拦截消息：不进入循环，返回拦截原因
        if hook_result is not None and hook_result.decided:
            yield StreamEvent(
                type="error",
                content=f"Message blocked by UserPromptSubmit hook: {hook_result.reason}",
            )
            return

        # hook 返回的额外上下文合并进 user_context
        if hook_result is not None and hook_result.reason:
            if user_context is None:
                user_context = {}
            user_context["hook_context"] = hook_result.reason

        # 把 user 消息加到 mutable_messages（message_meta 携带队列转正行的观测字段）
        user_msg: dict[str, Any] = {"role": "user", "content": prompt, "_ts": time.time() * 1000}
        if message_meta:
            user_msg.update(message_meta)
        self._mutable_messages.append(user_msg)

        # 新用户回合：重置压缩连败与快速再满计数；
        # 冷却基线（last_compact_time）与上次压缩计数快照保留，跨回合生效
        self._compact_tracking.consecutive_failures = 0
        if hasattr(self._compact_tracking, "refill_within_rounds"):
            self._compact_tracking.refill_within_rounds = 0

        # 构建循环级快照（session_id 整个会话不变）
        query_config = build_query_config(session_id=self._session_id)

        # 调 query_loop
        async for event in query_loop(self, query_config, user_context, system_context):
            yield event

        # 循环结束，turn_count + 1
        self._turn_count += 1
