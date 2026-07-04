"""文件邮箱 — 代理间消息传递。

每个 teammate 一个 inbox：~/.agent/teams/{team}/inboxes/{agent_name}.json
通过文件锁串行化写入，防止并发竞争。

消息结构：{from, text, timestamp, read, summary}
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 文件锁重试参数
_LOCK_RETRY_COUNT = 10
_LOCK_RETRY_DELAY = 0.1  # 秒

# 结构化协议消息类型
STRUCTURED_PROTOCOL_TYPES = {
    "shutdown_request",
    "shutdown_response",
    "plan_approval",
}


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------


def _get_inbox_path(team_name: str, agent_name: str) -> Path:
    """获取 inbox 文件路径。"""
    from tools.team.manager import _get_inbox_dir
    return _get_inbox_dir(team_name) / f"{agent_name}.json"


# ---------------------------------------------------------------------------
# _acquire_lock / _release_lock — 跨平台文件锁
# ---------------------------------------------------------------------------


def _acquire_lock(lock_path: Path) -> Any:
    """获取文件锁（跨平台）。

    Windows 用 msvcrt.locking，Linux/Mac 用 fcntl.flock。
    返回锁句柄（文件对象），释放时传给 _release_lock。
    """
    lock_file = open(lock_path, "w")
    retry = 0
    while retry < _LOCK_RETRY_COUNT:
        try:
            if os.name == "nt":
                # Windows: msvcrt.locking
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                # Unix: fcntl.flock
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_file
        except (OSError, IOError):
            retry += 1
            time.sleep(_LOCK_RETRY_DELAY)

    lock_file.close()
    raise TimeoutError(f"Failed to acquire lock: {lock_path}")


def _release_lock(lock_handle: Any) -> None:
    """释放文件锁。"""
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        lock_handle.close()


# ---------------------------------------------------------------------------
# write_to_mailbox — 写入消息到邮箱
# ---------------------------------------------------------------------------


def write_to_mailbox(
    team_name: str,
    agent_name: str,
    text: str,
    *,
    sender: str = "leader",
    summary: str = "",
    msg_type: str = "normal",
) -> None:
    """写入消息到指定 teammate 的邮箱。

    用文件锁串行化写入，防止并发竞争。

    Args:
        team_name: 团队名
        agent_name: 接收者名字
        text: 消息正文
        sender: 发送者名字
        summary: 消息摘要（可选）
        msg_type: 消息类型（"normal" / "shutdown_request" 等结构化协议消息）
    """
    inbox_path = _get_inbox_path(team_name, agent_name)
    inbox_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建消息
    message = {
        "from": sender,
        "text": text,
        "summary": summary or text[:100],
        "timestamp": time.time(),
        "read": False,
        "msg_type": msg_type,
    }

    # 用文件锁写入
    lock_path = inbox_path.with_suffix(".lock")
    lock_handle = _acquire_lock(lock_path)

    try:
        # 读取现有消息
        messages: list[dict] = []
        if inbox_path.exists():
            try:
                messages = json.loads(inbox_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                messages = []

        # 追加新消息
        messages.append(message)

        # 写回
        inbox_path.write_text(
            json.dumps(messages, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        _release_lock(lock_handle)
        # 清理锁文件
        try:
            lock_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# read_inbox — 非阻塞读取并标记已读
# ---------------------------------------------------------------------------


def read_inbox(team_name: str, agent_name: str) -> list[dict[str, Any]]:
    """非阻塞读取 inbox 中所有未读消息，并标记为已读。

    Args:
        team_name: 团队名
        agent_name: 接收者名字

    Returns:
        未读消息列表（按时间排序）
    """
    inbox_path = _get_inbox_path(team_name, agent_name)
    if not inbox_path.exists():
        return []

    lock_path = inbox_path.with_suffix(".lock")
    lock_handle = _acquire_lock(lock_path)

    try:
        messages = json.loads(inbox_path.read_text(encoding="utf-8"))

        # 筛选未读
        unread = [m for m in messages if not m.get("read", False)]

        # 标记已读
        for m in messages:
            m["read"] = True

        inbox_path.write_text(
            json.dumps(messages, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return unread
    except (json.JSONDecodeError, ValueError):
        return []
    finally:
        _release_lock(lock_handle)
        try:
            lock_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# is_structured_protocol_message — 判断是否为结构化协议消息
# ---------------------------------------------------------------------------


def is_structured_protocol_message(message: dict) -> bool:
    """判断消息是否为结构化协议消息（如 shutdown_request）。

    结构化协议消息不作为对话内容注入，而是路由到专门处理。
    """
    return message.get("msg_type", "normal") in STRUCTURED_PROTOCOL_TYPES


# ---------------------------------------------------------------------------
# broadcast — 广播消息
# ---------------------------------------------------------------------------


def broadcast(
    team_name: str,
    text: str,
    *,
    sender: str = "leader",
    summary: str = "",
    exclude: str | None = None,
) -> list[str]:
    """向团队所有成员广播消息。

    Args:
        team_name: 团队名
        text: 消息正文
        sender: 发送者
        summary: 摘要
        exclude: 排除的成员名（通常是发送者自己）

    Returns:
        成功发送的成员名列表
    """
    from tools.team.manager import get_member_names

    members = get_member_names(team_name)
    sent_to: list[str] = []

    for member in members:
        if exclude and member == exclude:
            continue
        try:
            write_to_mailbox(
                team_name, member, text,
                sender=sender, summary=summary,
            )
            sent_to.append(member)
        except Exception as e:
            logger.warning("广播到 %s 失败: %s", member, e)

    return sent_to
