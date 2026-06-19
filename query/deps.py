"""依赖注入 — 核心 I/O 依赖。

依赖注入 — 核心 I/O 依赖。

将 query() 的 I/O 依赖抽为可替换的接口，
测试可直接注入 fake 而无需 spyOn-per-module。

压缩依赖拆分为两段：
- microcompact: 微压缩（不调 LLM），清空旧 tool_result 内容
- autocompact: 自动压缩（调 LLM），全量摘要
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable
from uuid import uuid4

from query.services.api.llm import StreamEvent, query_model_with_streaming
from query.services.compact.micro_compact import micro_compact_messages
from query.services.compact.auto_compact import auto_compact_if_needed
from tools.executor import ToolExecutionResult, execute_tool_calls


# ---------------------------------------------------------------------------
# 类型别名 — 依赖函数签名
# ---------------------------------------------------------------------------

# call_model: 调用 LLM，流式返回 StreamEvent
CallModelFn = Callable[..., AsyncGenerator[StreamEvent, None]]

# compact: 压缩函数（microcompact / autocompact 复用同一签名）
CompactFn = Callable[..., Any]

# microcompact: 微压缩（清空旧 tool_result，不调 LLM）
MicrocompactFn = CompactFn

# autocompact: 自动压缩（全量摘要，调 LLM）
AutocompactFn = CompactFn

# execute_tools: 执行工具调用
ExecuteToolsFn = Callable[..., Any]

# get_uuid: UUID 生成
GetUuidFn = Callable[[], str]


# ---------------------------------------------------------------------------
# QueryDeps — 核心 I/O 依赖
# ---------------------------------------------------------------------------


@dataclass
class QueryDeps:
    """查询核心 I/O 依赖。

    通过 deps 参数注入到 query()，让测试可以替换为 fake。
    压缩拆分为 microcompact（微压缩）和 autocompact（自动压缩）两段。

    Attributes:
        call_model: 模型调用函数（流式）
        microcompact: 微压缩函数（清空旧 tool_result，不调 LLM）
        autocompact: 自动压缩函数（全量摘要，调 LLM）
        execute_tools: 工具执行函数
        get_uuid: UUID 生成函数
    """

    call_model: CallModelFn
    microcompact: CompactFn
    autocompact: CompactFn
    execute_tools: ExecuteToolsFn
    get_uuid: GetUuidFn = field(default_factory=lambda: lambda: str(uuid4()))


# ---------------------------------------------------------------------------
# production_deps — 生产环境依赖工厂
# ---------------------------------------------------------------------------


def production_deps() -> QueryDeps:
    """创建生产环境依赖。

    Returns:
        QueryDeps，其中：
        - call_model → query_model_with_streaming
        - microcompact → micro_compact_messages
        - autocompact → auto_compact_if_needed
        - execute_tools → execute_tool_calls
        - get_uuid → uuid4
    """
    return QueryDeps(
        call_model=query_model_with_streaming,
        microcompact=micro_compact_messages,
        autocompact=auto_compact_if_needed,
        execute_tools=execute_tool_calls,
        get_uuid=lambda: str(uuid4()),
    )


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("依赖注入测试")
    print("=" * 60)

    # ---- 测试 1: production_deps 工厂 ----
    print("\n--- 测试 1: production_deps 工厂 ---")
    deps = production_deps()
    assert deps.call_model is query_model_with_streaming
    assert deps.microcompact is micro_compact_messages
    assert deps.autocompact is auto_compact_if_needed
    assert deps.execute_tools is execute_tool_calls
    print(f"  call_model: {deps.call_model.__name__}")
    print(f"  microcompact: {deps.microcompact.__name__}")
    print(f"  autocompact: {deps.autocompact.__name__}")
    print(f"  execute_tools: {deps.execute_tools.__name__}")
    print("  [PASS] production_deps 工厂")

    # ---- 测试 2: get_uuid 生成唯一 ID ----
    print("\n--- 测试 2: get_uuid 生成唯一 ID ---")
    deps = production_deps()
    id1 = deps.get_uuid()
    id2 = deps.get_uuid()
    assert isinstance(id1, str)
    assert isinstance(id2, str)
    assert id1 != id2
    print(f"  id1={id1}")
    print(f"  id2={id2}")
    print("  [PASS] get_uuid 生成唯一 ID")

    # ---- 测试 3: 自定义 deps ----
    print("\n--- 测试 3: 自定义 deps ---")

    async def mock_call_model(**kwargs):
        yield StreamEvent(type="content", content="mock response")

    async def mock_microcompact(**kwargs):
        return kwargs.get("messages", [])

    async def mock_autocompact(**kwargs):
        return kwargs.get("messages", []), False

    async def mock_execute_tools(**kwargs):
        return []

    custom_deps = QueryDeps(
        call_model=mock_call_model,
        microcompact=mock_microcompact,
        autocompact=mock_autocompact,
        execute_tools=mock_execute_tools,
        get_uuid=lambda: "fixed-uuid",
    )
    assert custom_deps.call_model is mock_call_model
    assert custom_deps.microcompact is mock_microcompact
    assert custom_deps.autocompact is mock_autocompact
    assert custom_deps.execute_tools is mock_execute_tools
    assert custom_deps.get_uuid() == "fixed-uuid"
    print(f"  call_model: {custom_deps.call_model.__name__}")
    print(f"  microcompact: {custom_deps.microcompact.__name__}")
    print(f"  autocompact: {custom_deps.autocompact.__name__}")
    print(f"  execute_tools: {custom_deps.execute_tools.__name__}")
    print(f"  get_uuid: {custom_deps.get_uuid()}")
    print("  [PASS] 自定义 deps")

    # ---- 测试 4: QueryDeps dataclass 字段 ----
    print("\n--- 测试 4: QueryDeps dataclass 字段 ---")
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(QueryDeps)}
    assert field_names == {"call_model", "microcompact", "autocompact", "execute_tools", "get_uuid"}
    print(f"  字段: {field_names}")
    print("  [PASS] QueryDeps dataclass 字段")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
