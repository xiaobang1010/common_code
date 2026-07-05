"""Sidechain transcript 持久化 — 子代理执行过程记录与恢复。

子代理执行期间每条消息增量写入 JSONL 文件，支持执行过程追溯和 resume。

存储格式：
    ~/.agent/subagents/{agent_id}/
    ├── transcript.jsonl    # 每行一条消息（JSON 对象）
    └── meta.json           # 元数据（agent_type/description/started_at/model）

JSONL 每行格式：
    {"uuid": "msg_001", "parentUuid": null, "role": "user", "content": "...", "agentId": "agent_abc", "isSidechain": true, "timestamp": 1234567890.0}

resume 时读取全部行 → 按 parentUuid 链重建线性消息列表。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid as uuid_module
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------


def _get_subagents_base_dir() -> Path:
    """获取子代理 transcript 基础目录。"""
    home = Path(os.path.expanduser("~"))
    return home / ".agent" / "subagents"


def _get_agent_dir(agent_id: str) -> Path:
    """获取子代理目录路径。"""
    return _get_subagents_base_dir() / agent_id


def _get_transcript_path(agent_id: str) -> Path:
    """获取 transcript JSONL 文件路径。"""
    return _get_agent_dir(agent_id) / "transcript.jsonl"


def _get_meta_path(agent_id: str) -> Path:
    """获取 meta.json 文件路径。"""
    return _get_agent_dir(agent_id) / "meta.json"


def _get_result_path(agent_id: str) -> Path:
    """获取完整结果落盘路径。"""
    return _get_agent_dir(agent_id) / "result.txt"


# ---------------------------------------------------------------------------
# write_agent_metadata — 写入元数据
# ---------------------------------------------------------------------------


def write_agent_metadata(
    agent_id: str,
    agent_type: str,
    description: str = "",
    model: str = "",
) -> None:
    """写入子代理元数据到 meta.json。

    Args:
        agent_id: 子代理 ID
        agent_type: 代理类型（如 "general-purpose"）
        description: 任务描述
        model: 使用的模型名
    """
    meta = {
        "agent_type": agent_type,
        "description": description,
        "started_at": time.time(),
        "model": model,
    }

    meta_path = _get_meta_path(agent_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.debug("写入子代理元数据: %s", meta_path)


# ---------------------------------------------------------------------------
# read_agent_metadata — 读取元数据
# ---------------------------------------------------------------------------


def read_agent_metadata(agent_id: str) -> dict[str, Any] | None:
    """读取子代理元数据。

    Returns:
        元数据字典，文件不存在返回 None
    """
    meta_path = _get_meta_path(agent_id)
    if not meta_path.exists():
        return None

    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取元数据失败 %s: %s", meta_path, e)
        return None


# ---------------------------------------------------------------------------
# record_sidechain_transcript — 增量写入消息
# ---------------------------------------------------------------------------


def record_sidechain_transcript(
    messages: list[dict],
    agent_id: str,
    last_uuid: str | None = None,
) -> str | None:
    """增量写入消息到 transcript JSONL 文件。

    每条消息追加一行，维护 parentUuid 链。
    O(1) 增量写入——只写传入的消息，不重写整个文件。

    Args:
        messages: 要写入的消息列表
        agent_id: 子代理 ID
        last_uuid: 上一条消息的 uuid（用于构建父链），None 表示从头部开始

    Returns:
        最后一条消息的 uuid（供下次调用传入 last_uuid）
    """
    transcript_path = _get_transcript_path(agent_id)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    current_parent = last_uuid

    with open(transcript_path, "a", encoding="utf-8") as f:
        for msg in messages:
            msg_uuid = str(uuid_module.uuid4())

            # 构建 transcript 行
            entry = {
                "uuid": msg_uuid,
                "parentUuid": current_parent,
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                "agentId": agent_id,
                "isSidechain": True,
                "timestamp": time.time(),
            }

            # 保留额外字段（tool_calls 等）
            if "tool_calls" in msg:
                entry["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                entry["tool_call_id"] = msg["tool_call_id"]

            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            current_parent = msg_uuid

    return current_parent


# ---------------------------------------------------------------------------
# get_agent_transcript — 读取并重建消息列表
# ---------------------------------------------------------------------------


def get_agent_transcript(agent_id: str) -> list[dict] | None:
    """从 transcript JSONL 文件读取并重建消息列表。

    按 parentUuid 链构建线性对话，过滤掉非本 agent 的消息。
    过滤未完成的 tool_use 和空白 assistant 消息。

    Args:
        agent_id: 子代理 ID

    Returns:
        重建后的消息列表，文件不存在返回 None
    """
    transcript_path = _get_transcript_path(agent_id)
    if not transcript_path.exists():
        return None

    try:
        lines = transcript_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError as e:
        logger.warning("读取 transcript 失败 %s: %s", transcript_path, e)
        return None

    if not lines:
        return None

    # 解析所有行
    all_entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
            all_entries.append(entry)
        except json.JSONDecodeError:
            continue

    # 过滤出本 agent 的 sidechain 消息
    agent_entries = [
        e for e in all_entries
        if e.get("agentId") == agent_id and e.get("isSidechain")
    ]

    if not agent_entries:
        return None

    # 按 parentUuid 链重建线性顺序
    uuid_to_entry = {e["uuid"]: e for e in agent_entries}
    parent_uuids = {e.get("parentUuid") for e in agent_entries}

    # 找叶节点（没有子消息的）
    leaf = None
    for entry in agent_entries:
        if entry["uuid"] not in parent_uuids:
            leaf = entry
            break

    if leaf is None:
        # 可能是循环链，取最后一条
        leaf = agent_entries[-1]

    # 从叶节点回溯到根
    chain: list[dict] = []
    current = leaf
    seen = set()
    while current is not None and current["uuid"] not in seen:
        chain.append(current)
        seen.add(current["uuid"])
        parent_uuid = current.get("parentUuid")
        current = uuid_to_entry.get(parent_uuid) if parent_uuid else None

    chain.reverse()

    # 转换为标准消息格式
    messages: list[dict] = []
    for entry in chain:
        msg: dict[str, Any] = {"role": entry["role"], "content": entry["content"]}
        if "tool_calls" in entry:
            msg["tool_calls"] = entry["tool_calls"]
        if "tool_call_id" in entry:
            msg["tool_call_id"] = entry["tool_call_id"]
        messages.append(msg)

    # 过滤未完成的 tool_use（assistant 有 tool_calls 但没有对应的 tool result）
    messages = _filter_unresolved_tool_uses(messages)

    # 过滤空白 assistant 消息
    messages = [
        m for m in messages
        if not (m["role"] == "assistant" and not m.get("content", "").strip()
                and not m.get("tool_calls"))
    ]

    return messages if messages else None


# ---------------------------------------------------------------------------
# _filter_unresolved_tool_uses — 过滤未完成的 tool_use
# ---------------------------------------------------------------------------


def _filter_unresolved_tool_uses(messages: list[dict]) -> list[dict]:
    """过滤掉没有对应 tool result 的 tool_use。

    assistant 消息有 tool_calls 但后面的 tool 消息不完整时，
    移除该 assistant 消息的 tool_calls。
    """
    result: list[dict] = []
    for i, msg in enumerate(messages):
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            # 检查后续是否有对应的 tool result
            tool_call_ids = {tc.get("id") for tc in msg["tool_calls"]}
            has_results = False
            for j in range(i + 1, len(messages)):
                if messages[j]["role"] == "tool":
                    if messages[j].get("tool_call_id") in tool_call_ids:
                        has_results = True
                        break
                elif messages[j]["role"] == "assistant":
                    # 遇到下一个 assistant，停止
                    break
            if not has_results:
                # 移除 tool_calls，保留 content
                filtered = {k: v for k, v in msg.items() if k != "tool_calls"}
                if filtered.get("content"):
                    result.append(filtered)
                # 没有 content 也没有 tool result → 丢弃
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


# ---------------------------------------------------------------------------
# save_full_result — 保存完整结果（截断时落盘）
# ---------------------------------------------------------------------------


def save_full_result(agent_id: str, content: str) -> str:
    """保存完整结果到文件（结果截断时使用）。

    Args:
        agent_id: 子代理 ID
        content: 完整结果文本

    Returns:
        文件路径
    """
    result_path = _get_result_path(agent_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(content, encoding="utf-8")
    return str(result_path)
