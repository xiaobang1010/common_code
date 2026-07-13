"""会话和工作区的数据模型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Session:
    """一次对话会话。

    Attributes:
        id: 会话唯一标识（UUID）
        workspace_path: 所属工作区路径
        title: 会话标题（自动取首条用户消息前 40 字符）
        branch: 创建时的 git 分支名
        created_at: 创建时间（ISO 格式）
        updated_at: 最后更新时间（ISO 格式）
        messages: OpenAI 格式消息列表
        message_count: 消息数量（列表场景下避免传输完整 messages）
    """

    id: str
    workspace_path: str
    title: str
    branch: str
    created_at: str  # ISO 格式
    updated_at: str
    messages: list[dict]
    message_count: int = 0


@dataclass
class Workspace:
    """工作区（项目目录）。

    Attributes:
        path: 工作区绝对路径
        name: 工作区名称（取路径 basename）
        last_used_at: 最后使用时间（ISO 格式）
        session_count: 该工作区下的会话数量
    """

    path: str
    name: str
    last_used_at: str
    session_count: int = 0
