"""消息切片辅助函数。

提供 compact boundary 查找和切片功能，让 query loop 和 REPL 共用同一套
"从最后一个压缩边界开始取活跃窗口"的语义。
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# boundary 识别
# ---------------------------------------------------------------------------

# compact_conversation() 生成的 boundary marker content 前缀
# 形如: "[Compact Boundary — auto — pre-compact tokens: 12345]"
_COMPACT_BOUNDARY_PREFIX = "[Compact Boundary"


def is_compact_boundary_message(message: dict) -> bool:
    """判断消息是否为 compact boundary marker。

    boundary marker 的 role 为 system，content 以 "[Compact Boundary" 开头。
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") != "system":
        return False
    content = message.get("content", "")
    if not isinstance(content, str):
        return False
    return content.startswith(_COMPACT_BOUNDARY_PREFIX)


# ---------------------------------------------------------------------------
# 查找与切片
# ---------------------------------------------------------------------------


def find_last_compact_boundary_index(messages: list[dict]) -> int:
    """从后往前查找最后一个 compact boundary marker 的索引。

    Args:
        messages: 消息列表

    Returns:
        最后一个 boundary 的索引；未找到返回 -1
    """
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            return i
    return -1


def get_messages_after_compact_boundary(messages: list[dict]) -> list[dict]:
    """从最后一个 compact boundary 开始切片（含 boundary）。

    无 boundary 时返回全部消息。

    Args:
        messages: 完整消息列表（可能含历史 boundary marker）

    Returns:
        最后一个 boundary 起的切片；无 boundary 则返回原列表
    """
    boundary_idx = find_last_compact_boundary_index(messages)
    if boundary_idx == -1:
        return messages
    return messages[boundary_idx:]


# ---------------------------------------------------------------------------
# skill 正文消息识别
# ---------------------------------------------------------------------------

# skill 正文消息用 <system-reminder> 包裹
_SYSTEM_REMINDER_PREFIX = "<system-reminder>"


def is_skill_message(message: dict) -> bool:
    """判断消息是否为 skill 正文（不参与压缩，需原样保留）。

    skill 正文由 SkillTool 注入，格式为 role=user、content 以
    <system-reminder> 开头。user_context 虽然也用此格式，但它是临时
    注入不写回引擎，因此引擎持久化消息中的此类消息只有 skill 正文。
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") != "user":
        return False
    content = message.get("content", "")
    if not isinstance(content, str):
        return False
    return content.strip().startswith(_SYSTEM_REMINDER_PREFIX)
