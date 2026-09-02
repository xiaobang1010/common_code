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
        prompt: str,
        user_context: dict[str, str] | None = None,
        system_context: dict[str, str] | None = None,
    ) -> AsyncGenerator[Any, None]:
        """提交用户输入，启动一轮 agentic 循环。

        把 user 消息追加到 mutable_messages，构建循环级快照，
        调 query_loop，循环结束后 turn_count + 1。

        Args:
            prompt: 用户输入文本
            user_context: 用户上下文字典
            system_context: 系统上下文字典

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

        hook_snapshot = get_hooks_snapshot()
        hook_result = None
        if hook_snapshot is not None:
            try:
                hook_result = await run_user_prompt_submit_hooks(
                    hook_snapshot,
                    prompt,
                    self._session_id,
                    effective_root(),
                )
            except Exception:
                hook_result = None
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

        # 把 user 消息加到 mutable_messages
        self._mutable_messages.append({"role": "user", "content": prompt, "_ts": time.time() * 1000})

        # 构建循环级快照（session_id 整个会话不变）
        query_config = build_query_config(session_id=self._session_id)

        # 调 query_loop
        async for event in query_loop(self, query_config, user_context, system_context):
            yield event

        # 循环结束，turn_count + 1
        self._turn_count += 1
