"""查询引擎 — 会话级状态持有者。

查询引擎 — 会话级状态持有者。

QueryEngine 持有会话状态（消息历史、token 用量、轮次），
跨多次 submitMessage 持久化。QueryEngineConfig 是会话级不可变配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from query.config import build_query_config
from query.deps import QueryDeps, production_deps


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
        deps: I/O 依赖
    """

    cwd: str = ""
    model: str = "gpt-4o"
    max_tokens: int = 8192
    temperature: float = 1.0
    permission_mode: str = "default"
    tools: list[Any] = field(default_factory=list)
    system_prompt_sections: list[Any] = field(default_factory=list)
    max_turns: int | None = None
    deps: QueryDeps = field(default_factory=production_deps)


# ---------------------------------------------------------------------------
# build_engine_config — 工厂函数
# ---------------------------------------------------------------------------


def build_engine_config(**overrides: Any) -> QueryEngineConfig:
    """构建会话级配置，从环境变量读默认值。

    环境变量映射：
      - COMMON_CODE_MODEL → model（默认 "gpt-4o"）
      - COMMON_CODE_MAX_TOKENS → max_tokens（默认 8192）
      - COMMON_CODE_TEMPERATURE → temperature（默认 1.0）
      - COMMON_CODE_PERMISSION_MODE → permission_mode（默认 "default"）

    其余字段（cwd、tools、system_prompt_sections、max_turns、deps）
    使用 dataclass 默认值。

    Args:
        **overrides: 覆盖字段值

    Returns:
        QueryEngineConfig 不可变会话级配置
    """
    defaults: dict[str, Any] = {
        "model": os.environ.get("COMMON_CODE_MODEL", "gpt-4o"),
        "max_tokens": int(os.environ.get("COMMON_CODE_MAX_TOKENS", "8192")),
        "temperature": float(os.environ.get("COMMON_CODE_TEMPERATURE", "1.0")),
        "permission_mode": os.environ.get("COMMON_CODE_PERMISSION_MODE", "default"),
    }
    defaults.update(overrides)
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
    ) -> None:
        self._config = config
        self._deps = config.deps
        self._mutable_messages: list[dict] = initial_messages or []
        self._total_usage: int = 0
        self._turn_count: int = 0
        # 整个会话一个 sessionId
        self._session_id: str = config.deps.get_uuid()

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

        # 把 user 消息加到 mutable_messages
        self._mutable_messages.append({"role": "user", "content": prompt})

        # 构建循环级快照（session_id 整个会话不变）
        query_config = build_query_config(session_id=self._session_id)

        # 调 query_loop
        async for event in query_loop(self, query_config, user_context, system_context):
            yield event

        # 循环结束，turn_count + 1
        self._turn_count += 1
