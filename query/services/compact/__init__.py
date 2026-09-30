"""压缩模块入口。

聚合压缩各级别模块的接口：micro_compact（微压缩）与 auto_compact（自动压缩），
另含工程压缩层 engineering（供 auto_compact 内部调用）。
管线的编排逻辑内联在 query/loop.py 的 _run_inline_compression，
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
from query.services.compact.micro_compact import (
    micro_compact_messages,
    should_micro_compact,
)

__all__ = [
    # Microcompact
    "should_micro_compact",
    "micro_compact_messages",
    # Autocompact
    "CompactTracking",
    "AUTOCOMPACT_BUFFER_TOKENS",
    "should_auto_compact",
    "auto_compact_if_needed",
    "compact_conversation",
    "get_auto_compact_threshold",
]
