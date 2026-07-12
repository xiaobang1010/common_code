"""写入保护 - 剥离有害字符，防止 FTS5 索引损坏。

剥离 lone UTF-16 surrogates 和 NUL 字节。
"""

from __future__ import annotations

import re

# Lone UTF-16 surrogates pattern
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_content(content: str) -> str:
    """清理内容，剥离有害字符。

    - 剥离 lone UTF-16 surrogates（\\ud800-\\udfff 孤立代理）
    - 剥离 NUL 字节（\\x00）

    Args:
        content: 原始内容

    Returns:
        清理后的内容
    """
    if not content:
        return content
    # Remove NUL bytes
    result = content.replace("\x00", "")
    # Remove lone surrogates
    result = _SURROGATE_RE.sub("", result)
    return result
