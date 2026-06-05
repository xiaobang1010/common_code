"""auto 模式 AI 分类器。

在 auto 权限模式下，使用 LLM 判断工具输入是否安全，
自动决定 allow 或 deny，超过拒绝计数限制时回退到 ASK（人工审批）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.utils.permissions.permissions import PermissionDecision

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 分类 prompt 模板
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM_PROMPT = """\
You are a permission classifier for an AI coding assistant.
Your job is to decide whether a tool invocation is safe to execute automatically.

Given the tool name and its input, respond with exactly one word:
- "allow" if the action is safe and should proceed automatically
- "deny" if the action is potentially dangerous and should require human approval

Guidelines:
- File reads, searches, and non-destructive operations are generally safe (allow)
- File writes, deletes, shell commands with side effects are generally risky (deny)
- Commands that modify system state, install packages, or access sensitive paths are risky (deny)
- Simple file edits in the working directory are usually safe (allow)
- Always prefer caution: when in doubt, choose "deny"
"""

_CLASSIFIER_USER_TEMPLATE = """\
Tool: {tool_name}
Input: {tool_input}

Should this tool invocation be allowed? Respond with "allow" or "deny".
"""


class AutoClassifier:
    """auto 模式 AI 分类器。

    使用 LLM 判断工具输入是否安全，超过拒绝计数限制时回退到人工审批。
    """

    MAX_CONSECUTIVE_DENIALS: int = 3
    MAX_TOTAL_DENIALS: int = 10

    def __init__(self) -> None:
        self._consecutive_denials: int = 0
        self._total_denials: int = 0

    def reset_denial_counts(self) -> None:
        """重置拒绝计数。"""
        self._consecutive_denials = 0
        self._total_denials = 0

    async def classify(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        llm_call_fn: Any | None = None,
    ) -> PermissionDecision:
        """AI 分类器决策。

        使用 LLM 判断工具输入是否安全。超过拒绝计数限制时回退到 ASK。

        Args:
            tool_name: 工具名。
            tool_input: 工具输入字典。
            llm_call_fn: 异步 LLM 调用函数，签名为
                async (messages: list[dict]) -> str
                接收消息列表，返回 LLM 文本响应。
                如果未提供，则默认返回 ASK。

        Returns:
            PermissionDecision 决策结果。
        """
        # 检查是否超过拒绝计数限制
        if self._should_fallback_to_ask():
            logger.warning(
                "AutoClassifier: denial limit reached "
                "(consecutive=%d, total=%d), falling back to ASK",
                self._consecutive_denials,
                self._total_denials,
            )
            return PermissionDecision.ASK

        # 如果没有提供 LLM 调用函数，回退到 ASK
        if llm_call_fn is None:
            logger.debug("AutoClassifier: no LLM call function provided, returning ASK")
            return PermissionDecision.ASK

        # 构建分类 prompt
        messages = self._build_messages(tool_name, tool_input)

        try:
            response = await llm_call_fn(messages)
            decision = self._parse_response(response)

            if decision == PermissionDecision.DENY:
                self._consecutive_denials += 1
                self._total_denials += 1
                logger.info(
                    "AutoClassifier: denied %s (consecutive=%d, total=%d)",
                    tool_name,
                    self._consecutive_denials,
                    self._total_denials,
                )
            else:
                # 成功时重置连续拒绝计数
                self._consecutive_denials = 0
                logger.debug("AutoClassifier: allowed %s", tool_name)

            return decision

        except Exception:
            logger.exception("AutoClassifier: LLM call failed, falling back to ASK")
            return PermissionDecision.ASK

    def _should_fallback_to_ask(self) -> bool:
        """检查是否应回退到人工审批。"""
        return (
            self._consecutive_denials >= self.MAX_CONSECUTIVE_DENIALS
            or self._total_denials >= self.MAX_TOTAL_DENIALS
        )

    def _build_messages(self, tool_name: str, tool_input: dict) -> list[dict]:
        """构建分类 prompt 消息列表。"""
        # 将 tool_input 序列化为可读格式
        try:
            input_str = json.dumps(tool_input, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            input_str = str(tool_input)

        user_content = _CLASSIFIER_USER_TEMPLATE.format(
            tool_name=tool_name,
            tool_input=input_str,
        )

        return [
            {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def _parse_response(self, response: str) -> PermissionDecision:
        """解析 LLM 响应为决策。

        在响应文本中查找 "allow" 或 "deny" 关键词。
        默认返回 DENY（fail-closed）。
        """
        text = response.strip().lower()

        if "allow" in text:
            return PermissionDecision.ALLOW
        if "deny" in text:
            return PermissionDecision.DENY

        # 无法解析时默认拒绝（fail-closed）
        logger.warning("AutoClassifier: unable to parse response '%s', defaulting to DENY", response[:100])
        return PermissionDecision.DENY
