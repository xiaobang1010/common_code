"""token 估算与统一上下文计数。

两套口径：
- `estimate_tokens` 系列（字符数 ÷ 4）：服务于非消息类估算——工具 schema、
  系统提示词段、技能列表字符预算等，不做触发判定。
- `count_context_tokens`（真实 usage 基线 + 消息增量按字符 ÷ 3 保守估算）：
  压缩触发、微压缩水位、容量面板共用的统一上下文计数口径。
"""

from __future__ import annotations

import json

# 默认 bytes-per-token 比率（约 4 字符 = 1 token）
BYTES_PER_TOKEN = 4

# 统一计数的消息增量估算比率（保守偏高，中文内容接近 1:1 token）
CONTEXT_CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。"""
    return max(1, len(text) // BYTES_PER_TOKEN)


def estimate_tokens_for_messages(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。

    Args:
        messages: 消息列表（dict 格式）

    Returns:
        估算的 token 数
    """
    return max(1, _chars_of_messages(messages) // BYTES_PER_TOKEN)


def count_context_tokens(messages: list[dict]) -> int:
    """统一上下文计数：真实 usage 基线 + 其后消息的字符÷3 增量估算。

    反向扫描活跃窗口内最近一条携带 `_context_usage`（API 真实返回的
    总输入 token，含缓存读写）的消息作为基线，其后新增消息按字符÷3
    估算增量；无任何 usage 记录时退回全量字符÷3 估算。
    口径以活跃窗口（最后一个压缩边界起）为准，边界前的历史不计入。

    Args:
        messages: 消息列表（可为含边界的完整历史，内部自行切片）

    Returns:
        上下文 token 计数
    """
    if not messages:
        return 0

    # 懒加载避免与 utils.messages 形成模块级循环依赖
    from query.utils.messages import get_messages_after_compact_boundary

    active = get_messages_after_compact_boundary(messages)

    base = 0
    start_idx = 0
    for i in range(len(active) - 1, -1, -1):
        usage = active[i].get("_context_usage")
        if isinstance(usage, (int, float)) and usage > 0:
            base = int(usage)
            start_idx = i + 1
            break

    total = base
    tail = active[start_idx:]
    if tail:
        total += _chars_of_messages(tail) // CONTEXT_CHARS_PER_TOKEN
    # 无 usage 且消息全为空串等极端形态：按至少 1 处理，避免 0 误判为空上下文
    return total if total > 0 else 1


def _chars_of_messages(messages: list[dict]) -> int:
    """消息序列化后的总字符数（估算共用）。"""
    return sum(len(json.dumps(msg, ensure_ascii=False)) for msg in messages)
