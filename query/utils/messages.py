"""消息切片与清洗辅助函数。

提供 compact boundary 查找和切片功能，让 query loop 和 REPL 共用同一套
"从最后一个压缩边界开始取活跃窗口"的语义；
提供悬空 tool_calls 清洗，保证发给模型与写入 DB 的历史序列合法。
"""

from __future__ import annotations

import time


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


# ---------------------------------------------------------------------------
# 悬空 tool_calls 清洗
# ---------------------------------------------------------------------------

# 缺失工具结果补入的合成文本：中断/崩溃导致的收尾不全是事实，如实告知模型
ABORTED_TOOL_RESULT_CONTENT = "[执行被中断，无结果]"


def _synthetic_tool_result(tool_call_id: str) -> dict:
    """构造一条合成 tool 结果消息（形状与 tool_result_to_openai_message 对齐）。"""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": ABORTED_TOOL_RESULT_CONTENT,
        "_ts": time.time() * 1000,
    }


def sanitize_dangling_tool_calls(messages: list[dict]) -> list[dict]:
    """补齐悬空 tool_calls 缺失的工具结果，返回新的消息列表。

    任务被中断（/api/abort 的 task.cancel 落在工具执行阶段）或输出超限
    恢复（finish_reason=length 的 7b 分支丢弃当轮工具结果）时，历史可能
    存在「assistant 带 tool_calls 但没有齐全对应 tool 结果」的残缺形态。
    这种序列发给 OpenAI 格式接口属于非法请求，部分模型服务宽容处理时会
    补执行旧工具调用——表现即「模型执行上一条消息」。

    全量扫描：每条带 tool_calls 的 assistant 消息，在其后、下一条非 tool
    消息之前，缺失结果的 id 就地补一条合成 tool 结果（位于既有结果之后，
    无既有结果则紧跟该 assistant）。不修改传入列表；历史合法时返回内容
    与原列表一致的副本。
    """
    sanitized: list[dict] = []
    # 最近一条带 tool_calls 的 assistant 消息中尚未等到结果的 id 集合
    pending_ids: set[str] = set()

    for msg in messages:
        if not isinstance(msg, dict):
            sanitized.append(msg)
            continue
        role = msg.get("role")

        if role == "assistant" and msg.get("tool_calls"):
            # 前一条 assistant 的悬空结果不可能再出现在另一条 assistant 之后，
            # 先在其前补齐再切换待补集合
            sanitized.extend(_synthetic_tool_result(i) for i in pending_ids)
            sanitized.append(msg)
            pending_ids = {
                tc.get("id") for tc in msg["tool_calls"] if isinstance(tc, dict) and tc.get("id")
            }
            continue

        if role == "tool" and pending_ids:
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id in pending_ids:
                pending_ids.discard(tool_call_id)
            sanitized.append(msg)
            continue

        # 非 tool 消息：还有没等到结果的 id 就在它之前补合成结果
        if pending_ids:
            sanitized.extend(_synthetic_tool_result(i) for i in pending_ids)
            pending_ids = set()
        sanitized.append(msg)

    # 历史以悬空 tool_calls 结尾（中断的典型形态）：在末尾补齐
    sanitized.extend(_synthetic_tool_result(i) for i in pending_ids)
    return sanitized
