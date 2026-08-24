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
        title: 会话标题（自动取首条用户消息前 40 字符，可手动重命名）
        branch: 创建时的 git 分支名
        created_at: 创建时间（ISO 格式）
        updated_at: 最后更新时间（ISO 格式）
        messages: OpenAI 格式消息列表
        message_count: 消息数量（列表场景下避免传输完整 messages）
        pinned: 是否置顶（持久状态，列表排序 pinned 优先）
        group_id: 所属自定义任务分组 id（空串表示未分组；分组只是视图标签，
            不改变任务的 workspace_path 归属）
    """

    id: str
    workspace_path: str
    title: str
    branch: str
    created_at: str  # ISO 格式
    updated_at: str
    messages: list[dict]
    message_count: int = 0
    pinned: bool = False
    group_id: str = ""


@dataclass
class Workspace:
    """工作区（项目目录）。

    Attributes:
        path: 工作区绝对路径
        name: 工作区名称（取路径 basename，可被 alias 覆盖显示）
        last_used_at: 最后使用时间（ISO 格式）
        session_count: 该工作区下的会话数量
        pinned: 是否置顶（排序 pinned 优先）
        alias: 别名（显示 alias||name，用于区分同名项目）
    """

    path: str
    name: str
    last_used_at: str
    session_count: int = 0
    pinned: bool = False
    alias: str = ""


@dataclass
class TaskGroup:
    """自定义任务分组（跨工作区的任务聚合视图标签）。

    Attributes:
        id: 分组唯一标识（UUID）
        name: 分组名称（必填，允许同名，以 id 区分）
        color: 颜色标识（十六进制色值，空串表示未设置）
        created_at: 创建时间（ISO 格式，列表按此升序）
    """

    id: str
    name: str
    color: str = ""
    created_at: str = ""
