"""团队管理 — TeamCreate/TeamDelete。

团队配置持久化到 ~/.agent/teams/{team}/config.json，
任务列表目录 ~/.agent/tasks/{team}/。
一个 leader 只能管理一个团队。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TEAMS_DIR = "teams"
TASKS_DIR = "tasks"
INBOXES_DIR = "inboxes"
CONFIG_FILE = "config.json"


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------


def _get_agent_home() -> Path:
    """获取 ~/.agent 目录。"""
    return Path(os.path.expanduser("~")) / ".agent"


def _get_team_dir(team_name: str) -> Path:
    """获取团队目录路径。"""
    return _get_agent_home() / TEAMS_DIR / team_name


def _get_tasks_dir(team_name: str) -> Path:
    """获取任务列表目录路径。"""
    return _get_agent_home() / TASKS_DIR / team_name


def _get_inbox_dir(team_name: str) -> Path:
    """获取邮箱目录路径。"""
    return _get_team_dir(team_name) / INBOXES_DIR


# ---------------------------------------------------------------------------
# 当前 leader 的团队（进程内单例）
# ---------------------------------------------------------------------------

_current_team: str | None = None


def get_current_team() -> str | None:
    """获取当前 leader 关联的团队名。"""
    return _current_team


def _set_current_team(team_name: str | None) -> None:
    """设置当前 leader 关联的团队名。"""
    global _current_team
    _current_team = team_name


# ---------------------------------------------------------------------------
# TeamConfig — 团队配置
# ---------------------------------------------------------------------------


def _team_config(team_name: str, mode: str = "in-process") -> dict[str, Any]:
    """构建团队配置字典。"""
    return {
        "team_name": team_name,
        "mode": mode,
        "members": [],  # [{"name": ..., "agent_id": ...}]
        "created_at": _now_iso(),
    }


def _now_iso() -> str:
    """当前时间 ISO 格式。"""
    from datetime import datetime
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# create_team — 创建团队
# ---------------------------------------------------------------------------


def create_team(team_name: str, mode: str = "in-process") -> dict[str, Any]:
    """创建团队。

    创建团队配置目录和任务列表目录。一个 leader 只能管一个团队。

    Args:
        team_name: 团队名
        mode: 部署模式（当前仅 "in-process"）

    Returns:
        {"ok": True, "team_name": ..., "config_path": ...}

    Raises:
        ValueError: leader 已关联团队，或团队名已存在
    """
    # leader 唯一团队约束
    if _current_team is not None:
        raise ValueError(
            f"Leader already manages team '{_current_team}'. "
            "Delete it before creating a new one."
        )

    team_dir = _get_team_dir(team_name)
    if team_dir.exists():
        raise ValueError(f"Team '{team_name}' already exists")

    # 创建目录
    team_dir.mkdir(parents=True, exist_ok=True)
    _get_tasks_dir(team_name).mkdir(parents=True, exist_ok=True)
    _get_inbox_dir(team_name).mkdir(parents=True, exist_ok=True)

    # 写配置
    config = _team_config(team_name, mode)
    config_path = team_dir / CONFIG_FILE
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _set_current_team(team_name)
    logger.info("团队创建: %s (mode=%s)", team_name, mode)

    return {
        "ok": True,
        "team_name": team_name,
        "config_path": str(config_path),
    }


# ---------------------------------------------------------------------------
# delete_team — 解散团队
# ---------------------------------------------------------------------------


def delete_team(team_name: str) -> dict[str, Any]:
    """解散团队，删除配置和任务目录。

    Args:
        team_name: 团队名

    Returns:
        {"ok": True, "team_name": ...}
    """
    import shutil

    team_dir = _get_team_dir(team_name)
    tasks_dir = _get_tasks_dir(team_name)

    if not team_dir.exists():
        raise ValueError(f"Team '{team_name}' does not exist")

    # 删除目录
    shutil.rmtree(team_dir, ignore_errors=True)
    shutil.rmtree(tasks_dir, ignore_errors=True)

    if _current_team == team_name:
        _set_current_team(None)

    logger.info("团队解散: %s", team_name)
    return {"ok": True, "team_name": team_name}


# ---------------------------------------------------------------------------
# team_exists — 检查团队是否存在
# ---------------------------------------------------------------------------


def team_exists(team_name: str) -> bool:
    """检查团队是否存在。"""
    return _get_team_dir(team_name).exists()


# ---------------------------------------------------------------------------
# add_member / remove_member / get_members — 成员管理
# ---------------------------------------------------------------------------


def add_member(team_name: str, agent_name: str, agent_id: str) -> None:
    """添加团队成员。"""
    config_path = _get_team_dir(team_name) / CONFIG_FILE
    if not config_path.exists():
        raise ValueError(f"Team '{team_name}' config not found")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    members = config.get("members", [])

    # 去重：同名覆盖
    for m in members:
        if m.get("name") == agent_name:
            m["agent_id"] = agent_id
            break
    else:
        members.append({"name": agent_name, "agent_id": agent_id})

    config["members"] = members
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def remove_member(team_name: str, agent_name: str) -> None:
    """移除团队成员。"""
    config_path = _get_team_dir(team_name) / CONFIG_FILE
    if not config_path.exists():
        return

    config = json.loads(config_path.read_text(encoding="utf-8"))
    members = config.get("members", [])
    config["members"] = [m for m in members if m.get("name") != agent_name]
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_members(team_name: str) -> list[dict[str, str]]:
    """获取团队成员列表。"""
    config_path = _get_team_dir(team_name) / CONFIG_FILE
    if not config_path.exists():
        return []
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return config.get("members", [])


def get_member_names(team_name: str) -> list[str]:
    """获取团队成员名字列表。"""
    return [m["name"] for m in get_members(team_name)]


# ---------------------------------------------------------------------------
# recover_crashed_teammates — 崩溃恢复
# ---------------------------------------------------------------------------


def recover_crashed_teammates(team_name: str) -> list[str]:
    """扫描崩溃的 teammate，释放其任务供重新认领。

    检查团队的 transcript 目录，对于有 transcript 但状态为 stopped 的 teammate，
    将其名下 in_progress 的任务回退为 pending 并清除 owner。

    Args:
        team_name: 团队名

    Returns:
        恢复的 teammate 名字列表
    """
    import logging
    logger = logging.getLogger(__name__)

    recovered: list[str] = []
    members = get_members(team_name)

    for member in members:
        agent_id = member.get("agent_id", "")
        agent_name = member.get("name", "")
        if not agent_id:
            continue

        # 检查是否有 transcript
        try:
            from tools.subagent.transcript import get_agent_transcript
            transcript = get_agent_transcript(agent_id)
            if transcript is None:
                continue  # 没有 transcript，不是崩溃的
        except Exception:
            continue

        # 检查是否在活跃注册表中
        from tools.team.lifecycle import get_teammate_status
        status = get_teammate_status(agent_name)
        if status == "running":
            continue  # 还在运行，不需要恢复

        # teammate 崩溃了（有 transcript 但不在运行）
        logger.info("检测到崩溃的 teammate: %s (last status: %s)", agent_name, status)
        recovered.append(agent_name)

        # 释放其 in_progress 任务
        try:
            from tools.team.task_tools import _list_all_tasks, _write_task
            tasks = _list_all_tasks(team_name)
            for task in tasks:
                if (
                    task.get("owner") == agent_name
                    and task.get("status") == "in_progress"
                ):
                    task["status"] = "pending"
                    task["owner"] = None
                    _write_task(team_name, task["id"], task)
                    logger.info("释放任务 #%d (原 owner: %s)", task["id"], agent_name)
        except Exception as e:
            logger.warning("释放崩溃 teammate 任务失败: %s", e)

    return recovered
