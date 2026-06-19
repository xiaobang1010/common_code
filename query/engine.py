"""查询引擎 — 会话级状态持有者。

参考原始 TypeScript 实现 src/query/engine.ts。

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
        # 整个会话一个 sessionId，对齐 TS 版 getSessionId()
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

        # 构建循环级快照（session_id 整个会话不变，对齐 TS 版）
        query_config = build_query_config(session_id=self._session_id)

        # 调 query_loop
        async for event in query_loop(self, query_config, user_context, system_context):
            yield event

        # 循环结束，turn_count + 1
        self._turn_count += 1


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    from query.deps import QueryDeps
    from query.services.api.llm import StreamEvent

    print("=" * 60)
    print("查询引擎测试")
    print("=" * 60)

    # ---- 测试 1: 构造 QueryEngine ----
    print("\n--- 测试 1: 构造 QueryEngine ---")
    config = QueryEngineConfig(model="test-model", max_tokens=4096)
    engine = QueryEngine(config)
    assert engine.config is config
    assert engine.config.model == "test-model"
    assert engine.config.max_tokens == 4096
    assert engine.mutable_messages == []
    assert engine.total_usage == 0
    assert engine.turn_count == 0
    assert engine.messages == []
    print(f"  model={engine.config.model}, max_tokens={engine.config.max_tokens}")
    print(f"  mutable_messages={engine.mutable_messages}, turn_count={engine.turn_count}")
    print("  [PASS] 构造 QueryEngine")

    # ---- 测试 2: build_engine_config 从环境变量读默认值 ----
    print("\n--- 测试 2: build_engine_config 从环境变量读默认值 ---")

    old_model = os.environ.get("COMMON_CODE_MODEL")
    old_max_tokens = os.environ.get("COMMON_CODE_MAX_TOKENS")
    old_temp = os.environ.get("COMMON_CODE_TEMPERATURE")
    old_perm = os.environ.get("COMMON_CODE_PERMISSION_MODE")

    os.environ["COMMON_CODE_MODEL"] = "claude-3"
    os.environ["COMMON_CODE_MAX_TOKENS"] = "2048"
    os.environ["COMMON_CODE_TEMPERATURE"] = "0.5"
    os.environ["COMMON_CODE_PERMISSION_MODE"] = "plan"
    try:
        cfg = build_engine_config()
        assert cfg.model == "claude-3", f"期望 claude-3, 得到 {cfg.model}"
        assert cfg.max_tokens == 2048, f"期望 2048, 得到 {cfg.max_tokens}"
        assert cfg.temperature == 0.5, f"期望 0.5, 得到 {cfg.temperature}"
        assert cfg.permission_mode == "plan", f"期望 plan, 得到 {cfg.permission_mode}"
        print(f"  model={cfg.model}, max_tokens={cfg.max_tokens}, "
              f"temperature={cfg.temperature}, permission_mode={cfg.permission_mode}")
    finally:
        # 恢复环境变量
        for key, old in [
            ("COMMON_CODE_MODEL", old_model),
            ("COMMON_CODE_MAX_TOKENS", old_max_tokens),
            ("COMMON_CODE_TEMPERATURE", old_temp),
            ("COMMON_CODE_PERMISSION_MODE", old_perm),
        ]:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    # 测试 overrides 覆盖
    cfg2 = build_engine_config(model="gpt-4o", max_tokens=8192)
    assert cfg2.model == "gpt-4o"
    assert cfg2.max_tokens == 8192
    print(f"  overrides: model={cfg2.model}, max_tokens={cfg2.max_tokens}")
    print("  [PASS] build_engine_config 从环境变量读默认值")

    # ---- 测试 3: messages 只读属性 ----
    print("\n--- 测试 3: messages 只读属性 ---")
    initial = [{"role": "user", "content": "hello"}]
    engine2 = QueryEngine(QueryEngineConfig(), initial_messages=initial)
    assert engine2.messages == initial
    assert engine2.messages is engine2.mutable_messages
    # mutable_messages 可写
    engine2.mutable_messages = [{"role": "user", "content": "new"}]
    assert engine2.messages == [{"role": "user", "content": "new"}]
    print(f"  messages={engine2.messages}")
    print("  [PASS] messages 只读属性")

    # ---- 测试 4: submitMessage 基本流程 ----
    print("\n--- 测试 4: submitMessage 基本流程 ---")

    async def _test_submit_message():
        async def mock_call_model(**kwargs):
            yield StreamEvent(type="content", content="Hello back!")
            yield StreamEvent(type="done", finish_reason="stop")

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        cfg = QueryEngineConfig(model="test-model", max_tokens=4096, deps=mock_deps)
        eng = QueryEngine(cfg)

        events = []
        async for event in eng.submitMessage("Hi"):
            events.append(event)

        # 验证收到 content + done + assistant 消息
        content_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "content"]
        done_events = [e for e in events if isinstance(e, StreamEvent) and e.type == "done"]
        assistant_msgs = [e for e in events if isinstance(e, dict) and e.get("role") == "assistant"]

        assert len(content_events) > 0, f"期望 content 事件, 得到 {len(content_events)}"
        assert len(done_events) > 0, f"期望 done 事件, 得到 {len(done_events)}"
        assert done_events[0].finish_reason == "stop"
        assert len(assistant_msgs) == 1, f"期望 1 条 assistant, 得到 {len(assistant_msgs)}"
        assert assistant_msgs[0]["content"] == "Hello back!"

        # 验证引擎状态更新
        assert eng.turn_count == 1
        assert len(eng.mutable_messages) == 2  # user + assistant
        assert eng.mutable_messages[0]["role"] == "user"
        assert eng.mutable_messages[0]["content"] == "Hi"
        assert eng.mutable_messages[1]["role"] == "assistant"

        print(f"  收到 {len(events)} 个事件, turn_count={eng.turn_count}")
        print(f"  mutable_messages={len(eng.mutable_messages)} 条")

    asyncio.run(_test_submit_message())
    print("  [PASS] submitMessage 基本流程")

    # ---- 测试 5: 跨 turn 状态持久化 ----
    print("\n--- 测试 5: 跨 turn 状态持久化 ---")

    async def _test_persistence():
        call_count = 0

        async def mock_call_model(**kwargs):
            nonlocal call_count
            call_count += 1
            yield StreamEvent(type="content", content=f"Response {call_count}")
            yield StreamEvent(type="done", finish_reason="stop")

        def mock_microcompact(messages=None, **kwargs):
            return messages

        async def mock_autocompact(**kwargs):
            return kwargs.get("messages", []), False

        async def mock_execute_tools(**kwargs):
            return []

        mock_deps = QueryDeps(
            call_model=mock_call_model,
            microcompact=mock_microcompact,
            autocompact=mock_autocompact,
            execute_tools=mock_execute_tools,
            get_uuid=lambda: "test-uuid",
        )

        cfg = QueryEngineConfig(model="test-model", max_tokens=4096, deps=mock_deps)
        eng = QueryEngine(cfg)

        # 第一次 submitMessage
        async for _ in eng.submitMessage("Hello"):
            pass
        assert eng.turn_count == 1
        assert len(eng.mutable_messages) == 2
        print(f"  第一次: turn_count={eng.turn_count}, messages={len(eng.mutable_messages)}")

        # 第二次 submitMessage
        async for _ in eng.submitMessage("World"):
            pass
        assert eng.turn_count == 2
        assert len(eng.mutable_messages) == 4

        # 验证历史包含第一次的消息
        msgs = eng.mutable_messages
        assert msgs[0]["content"] == "Hello", f"期望 Hello, 得到 {msgs[0]['content']}"
        assert msgs[1]["content"] == "Response 1"
        assert msgs[2]["content"] == "World"
        assert msgs[3]["content"] == "Response 2"

        print(f"  第二次: turn_count={eng.turn_count}, messages={len(eng.mutable_messages)}")
        print(f"  历史: {[m['content'] for m in msgs]}")

    asyncio.run(_test_persistence())
    print("  [PASS] 跨 turn 状态持久化")

    # ---- 测试 6: frozen 不可变 ----
    print("\n--- 测试 6: QueryEngineConfig frozen 不可变 ---")
    cfg = QueryEngineConfig()
    try:
        cfg.model = "other"  # type: ignore
        assert False, "frozen dataclass 不应允许修改"
    except AttributeError:
        print("  frozen dataclass 修改被拒绝: OK")
    print("  [PASS] frozen 不可变")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
