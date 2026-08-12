"""ConversationMiner - 会话自动摄取：LLM 结构化抽取候选记忆。

会话结束时由 MemoryPalaceProvider.mine_conversation 调用：
对话 → LLM 抽取候选记忆（类型 + 置信度）→ 过滤 → 幂等写入。
LLM 不可用或解析失败时静默降级返回空列表，不阻断会话。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 候选记忆类型（与自动摄取的 room 一一对应）
CANDIDATE_TYPES = [
    "preference",       # 用户偏好
    "project_fact",     # 项目事实
    "environment",      # 环境信息
    "decision",         # 决策
    "constraint",       # 约束
    "api_usage",        # API 用法
    "bug_resolution",   # 问题解决
]

# 抽取提示词：要求模型只输出 JSON 数组，便于程序解析
_EXTRACTION_SYSTEM_PROMPT = (
    "你是记忆抽取器。从对话中抽取值得长期记住的信息，只输出一个 JSON 数组，"
    "每个元素包含三个字段：\n"
    '- "type"：候选类型，取以下之一：preference（用户偏好）、project_fact（项目事实）、'
    'environment（环境信息）、decision（决策）、constraint（约束）、'
    'api_usage（API 用法）、bug_resolution（问题解决）；\n'
    '- "content"：一句话描述，保留关键细节（文件名、API 名、报错信息等）；\n'
    '- "confidence"：0~1 之间的数字，表示这条信息值得长期保留的把握。\n'
    "只输出 JSON 数组，不要输出其他任何内容。没有值得记住的内容时输出 []。"
)


async def extract_candidates(convo_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从对话消息中抽取候选记忆，返回 [{"type", "content", "confidence"}]。

    LLM 调用失败、解析失败或无可抽取内容时返回 []（静默降级，不抛异常）。
    """
    convo_text = _flatten_conversation(convo_messages)
    if not convo_text.strip():
        return []
    try:
        # 惰性导入：避免 memory 包启动时反向依赖 query 服务
        from query.services.api.llm import query_model_with_streaming

        messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            # 只取对话末尾，防止超长对话撑爆上下文
            {"role": "user", "content": convo_text[-12000:]},
        ]
        parts: list[str] = []
        async for evt in query_model_with_streaming(messages=messages):
            if evt.type == "content" and evt.content:
                parts.append(evt.content)
        return _parse_candidates("".join(parts))
    except Exception as e:
        logger.warning("会话记忆抽取失败，跳过自动摄取: %s", e)
        return []


def _flatten_conversation(convo_messages: list[dict[str, Any]]) -> str:
    """把消息列表压平成角色标注文本。

    只保留 user/assistant 消息：工具结果等对记忆抽取价值低，且可能很长。
    """
    lines = []
    for m in convo_messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role not in ("user", "assistant") or not content:
            continue
        # OpenAI 格式的 content 可能是分段列表
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _parse_candidates(text: str) -> list[dict[str, Any]]:
    """解析模型输出的候选 JSON，容错处理各种包裹格式。"""
    if not text:
        return []
    cleaned = text.strip()
    # 去掉 ```json ... ``` 代码块包裹
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    # 定位第一个 [ 到最后一个 ]（模型可能夹带解释文字）
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    candidates: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("type", "")).strip()
        content = str(item.get("content", "")).strip()
        if ctype not in CANDIDATE_TYPES or not content:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        candidates.append({
            "type": ctype,
            "content": content,
            "confidence": max(0.0, min(1.0, confidence)),
        })
    return candidates
