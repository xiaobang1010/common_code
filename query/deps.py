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
        get_uuid: UUID 生成函数
    """

    call_model: CallModelFn
    microcompact: CompactFn
    autocompact: CompactFn
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
        - get_uuid → uuid4
    """
    return QueryDeps(
        call_model=query_model_with_streaming,
        microcompact=micro_compact_messages,
        autocompact=auto_compact_if_needed,
        get_uuid=lambda: str(uuid4()),
    )
