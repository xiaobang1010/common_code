"""粗略 token 估算（全项目统一口径）。

压缩触发判断与上下文容量分类估算共用同一函数，保证内部一致。
注意：tools/skills/listing.py 的 BYTES_PER_TOKEN 服务于技能列表字符预算
（按字节预算截断描述文本），口径不同，不并入本模块。
"""

from __future__ import annotations

import json

# 默认 bytes-per-token 比率（约 4 字符 = 1 token）
BYTES_PER_TOKEN = 4


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
    total_chars = 0
    for msg in messages:
        # 序列化整个消息为 JSON 字符串来估算
        total_chars += len(json.dumps(msg, ensure_ascii=False))
    return max(1, total_chars // BYTES_PER_TOKEN)
