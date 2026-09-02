"""子代理子会话绑定 — 会话化持久化入口。

子代理派生即创建（或复用）子会话，生命周期状态流转点同步 agent_meta。
单一写者原则：只有本模块写会话存储侧；会话存储不可用或写入失败时
降级为「注册表 + 转录」模式，子代理运行不受影响。
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 子会话 id 前缀（确定值，供 resume 重入复用）
CHILD_SESSION_PREFIX = "subagent_"


# ---------------------------------------------------------------------------
# id 与存储访问
# ---------------------------------------------------------------------------


def child_session_id_for(agent_id: str) -> str:
    """子代理的子会话 id（确定值，同一 agent_id 恒定）。"""
    return f"{CHILD_SESSION_PREFIX}{agent_id}"


def _get_session_store():
    """获取进程级会话存储（未装配返回 None）。"""
    try:
        import server.state

        return server.state.session_store
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 创建 / 复用（upsert）
# ---------------------------------------------------------------------------


def ensure_child_session(
    agent_id: str,
    *,
    parent_session_id: str | None,
    workspace_path: str,
    title: str,
    agent_type: str,
    mode: str,
) -> str | None:
    """按 upsert 语义落实子会话：已存在则复用，不存在才创建。

    覆盖 resume 重入场景（同一 agent_id 再次运行时复用旧会话行）。
    任何失败返回 None，调用方降级为无子会话模式。

    Args:
        agent_id: 子代理标识
        parent_session_id: 父会话标识
        workspace_path: 工作区路径
        title: 子会话标题（任务描述）
        agent_type: 代理类型
        mode: 运行模式（foreground / background）

    Returns:
        子会话 id；失败返回 None
    """
    store = _get_session_store()
    if store is None:
        return None
    session_id = child_session_id_for(agent_id)
    try:
        if not store.session_exists(session_id):
            store.create_session(
                workspace_path,
                title=title,
                session_id=session_id,
                origin="subagent",
                parent_session_id=parent_session_id,
            )
        # 初始元数据：状态 running，其余字段占位
        store.merge_session_agent_meta(
            session_id,
            {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "status": "running",
                "usage": {"total_tokens": 0, "tool_uses": 0, "duration_ms": 0},
                "output_file": None,
                "promoted": False,
                "mode": mode,
            },
        )
        return session_id
    except Exception as e:
        logger.warning("子会话创建/复用失败（降级为无子会话模式）: %s", e)
        return None


# ---------------------------------------------------------------------------
# 消息持久化与状态同步
# ---------------------------------------------------------------------------


def persist_child_messages(child_session_id: str, messages: list[dict]) -> None:
    """把子代理当前完整消息列表整表写入子会话（每轮调用）。

    失败仅日志，不阻断子代理运行（转录边车仍是崩溃恢复来源）。
    """
    store = _get_session_store()
    if store is None:
        return
    try:
        store.save_messages(child_session_id, messages)
    except Exception as e:
        logger.warning("子会话消息落库失败: %s", e)


def update_child_meta(
    agent_id: str,
    *,
    status: str | None = None,
    usage: dict | None = None,
    output_file: str | None = None,
    promoted: bool | None = None,
) -> None:
    """状态流转点同步子会话 agent_meta（部分字段合并）。

    子会话不存在或存储不可用时静默跳过。
    """
    store = _get_session_store()
    if store is None:
        return
    partial: dict = {}
    if status is not None:
        partial["status"] = status
    if usage is not None:
        partial["usage"] = usage
    if output_file is not None:
        partial["output_file"] = output_file
    if promoted is not None:
        partial["promoted"] = promoted
    if not partial:
        return
    try:
        store.merge_session_agent_meta(child_session_id_for(agent_id), partial)
    except Exception as e:
        logger.warning("子会话元数据同步失败: %s", e)
