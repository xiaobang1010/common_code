"""消息列表组件。

参考原始 TypeScript 实现: src/components/Messages.tsx, src/utils/messages.ts

提供消息数据模型、规范化、折叠和 UUID 锚点切片功能。
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# MessageData — 消息数据模型
# ---------------------------------------------------------------------------

@dataclass
class MessageData:
    """消息数据。

    Attributes:
        role: 消息角色 ("system" | "user" | "assistant" | "tool")
        content: 消息文本内容
        tool_calls: 工具调用列表 (仅 assistant 消息)
        tool_call_id: 工具调用 ID (仅 tool 消息)
        uuid: 消息唯一标识
        timestamp: 消息时间戳
        is_compact_boundary: 是否为压缩边界
    """

    role: str
    content: str = ""
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None
    uuid: str = ""
    timestamp: Optional[float] = None
    is_compact_boundary: bool = False

    def __post_init__(self) -> None:
        if not self.uuid:
            self.uuid = str(_uuid.uuid4())


# ---------------------------------------------------------------------------
# normalize_messages — 规范化消息列表
# ---------------------------------------------------------------------------

def normalize_messages(raw_messages: list[dict]) -> list[MessageData]:
    """将原始 dict 消息列表转换为 MessageData 列表。

    处理逻辑：
    - 为每条消息生成 UUID
    - 处理缺失字段
    - 识别压缩边界标记

    Args:
        raw_messages: 原始消息字典列表

    Returns:
        规范化后的 MessageData 列表
    """
    result: list[MessageData] = []

    for raw in raw_messages:
        role = raw.get("role", "user")
        content = raw.get("content", "")

        # 处理 content 为 list 的情况 (API 格式)
        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        # tool_use 块不合并到 content 文本
                        pass
                    elif block.get("type") == "tool_result":
                        text_parts.append(block.get("content", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts)

        # 提取 tool_calls
        tool_calls: list[dict] | None = None
        raw_content = raw.get("content")
        if isinstance(raw_content, list) and role == "assistant":
            tc_list: list[dict] = []
            for block in raw_content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tc_list.append({
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    })
            if tc_list:
                tool_calls = tc_list

        # 提取 tool_call_id
        tool_call_id: str | None = None
        if isinstance(raw_content, list):
            for block in raw_content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_call_id = block.get("tool_use_id")
                    break
        if tool_call_id is None:
            tool_call_id = raw.get("tool_call_id")

        # 判断是否为压缩边界
        is_compact_boundary = raw.get("is_compact_boundary", False)
        if not is_compact_boundary:
            # 检查 system 消息中的 subtype 标记
            if role == "system" and raw.get("subtype") == "compact_boundary":
                is_compact_boundary = True

        msg = MessageData(
            role=role,
            content=content if isinstance(content, str) else str(content),
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            uuid=raw.get("uuid", ""),
            timestamp=raw.get("timestamp"),
            is_compact_boundary=is_compact_boundary,
        )
        result.append(msg)

    return result


# ---------------------------------------------------------------------------
# fold_messages — 多级折叠管线
# ---------------------------------------------------------------------------

@dataclass
class _FoldedMessage(MessageData):
    """折叠占位消息。"""
    folded_count: int = 0


def fold_messages(
    messages: list[MessageData],
    max_visible: int = 50,
) -> list[MessageData]:
    """多级折叠管线。

    超过 max_visible 条消息时折叠中间部分，保留最近和最早的消息，
    折叠部分替换为 "[N messages compacted]"。

    Args:
        messages: 消息列表
        max_visible: 最大可见消息数

    Returns:
        折叠后的消息列表
    """
    if len(messages) <= max_visible:
        return list(messages)

    # 保留头部和尾部各 1/3，中间折叠
    head_count = max_visible // 3
    tail_count = max_visible - head_count - 1  # -1 给折叠占位

    head = messages[:head_count]
    tail = messages[-tail_count:]
    folded_count = len(messages) - head_count - tail_count

    # 创建折叠占位消息
    placeholder = MessageData(
        role="system",
        content=f"[{folded_count} messages compacted]",
        uuid=str(_uuid.uuid4()),
    )

    return head + [placeholder] + tail


# ---------------------------------------------------------------------------
# slice_by_uuid — UUID 锚点切片
# ---------------------------------------------------------------------------

def slice_by_uuid(
    messages: list[MessageData],
    start_uuid: Optional[str] = None,
    end_uuid: Optional[str] = None,
) -> list[MessageData]:
    """UUID 锚点切片。

    使用 UUID 作为锚点而非索引，避免压缩/折叠导致偏移。

    Args:
        messages: 消息列表
        start_uuid: 起始 UUID (包含)，None 表示从头开始
        end_uuid: 结束 UUID (包含)，None 表示到末尾

    Returns:
        切片后的消息列表
    """
    if not messages:
        return []

    start_idx = 0
    end_idx = len(messages)

    if start_uuid is not None:
        for i, msg in enumerate(messages):
            if msg.uuid == start_uuid:
                start_idx = i
                break

    if end_uuid is not None:
        for i, msg in enumerate(messages):
            if msg.uuid == end_uuid:
                end_idx = i + 1
                break

    return messages[start_idx:end_idx]
