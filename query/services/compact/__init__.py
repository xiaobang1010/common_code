"""压缩模块入口。

聚合各压缩级别模块的接口：snip、micro_compact、context_collapse、auto_compact。
四级压缩的编排逻辑已内联到 query/loop.py 的 _run_inline_compression，
本模块只负责导出各级别的判断与执行函数。
"""

from __future__ import annotations

from query.services.compact.auto_compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    CompactTracking,
    auto_compact_if_needed,
    compact_conversation,
    get_auto_compact_threshold,
    should_auto_compact,
)
from query.services.compact.context_collapse import (
    context_collapse_messages,
    should_context_collapse,
)
from query.services.compact.micro_compact import (
    micro_compact_messages,
    should_micro_compact,
)
from query.services.compact.snip import (
    should_snip,
    snip_messages,
)

__all__ = [
    # Snip
    "should_snip",
    "snip_messages",
    # Microcompact
    "should_micro_compact",
    "micro_compact_messages",
    # Context Collapse
    "should_context_collapse",
    "context_collapse_messages",
    # Autocompact
    "CompactTracking",
    "AUTOCOMPACT_BUFFER_TOKENS",
    "should_auto_compact",
    "auto_compact_if_needed",
    "compact_conversation",
    "get_auto_compact_threshold",
]
