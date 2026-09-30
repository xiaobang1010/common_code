"""确定性工程压缩层（不调 LLM）。

在调用 LLM 摘要之前，先把待压缩历史折叠为确定性 XML 文本：
用户与 assistant 消息保留文本、工具结果一律省略、旧摘要原文内联
（不再摘要，杜绝套娃退化）。折叠结果估算足够小（≤ 窗口×15%）时
直接采纳为压缩产物，省掉一次 LLM 调用；否则作为 LLM 摘要的输入底稿。
"""

from __future__ import annotations

from query.utils.tokens import CONTEXT_CHARS_PER_TOKEN
from query.services.compact.prompt import (
    _extract_message_text,
    _truncate_args,
)
from query.utils.messages import is_skill_message


def fold_messages(messages: list[dict]) -> tuple[str, int]:
    """把消息列表折叠为确定性工程压缩文本。

    Args:
        messages: 待折叠的消息列表（可含旧摘要与 skill 正文，按规则处理）

    Returns:
        (折叠文本, ÷3 口径估算 token 数)
    """
    parts: list[str] = []

    for msg in messages:
        if is_skill_message(msg):
            continue

        if msg.get("_compact_summary"):
            text = _extract_message_text(msg)
            if text.strip():
                parts.append(f"<cb_summary>\n{text}\n</cb_summary>")
            continue

        role = msg.get("role", "")

        if role == "user":
            text = _extract_message_text(msg)
            if text.strip():
                parts.append(f"<previous_user_message>{text}</previous_user_message>")

        elif role == "assistant":
            text = _extract_message_text(msg)
            if text.strip():
                parts.append(f"<previous_assistant_message>{text}</previous_assistant_message>")
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name", "tool")
                args = fn.get("arguments", "")
                if not isinstance(args, str):
                    import json
                    args = json.dumps(args, ensure_ascii=False)
                parts.append(
                    f'<previous_tool_call name="{name}" id="{tc.get("id", "")}">'
                    f"\n{_truncate_args(args)}"
                    f"\n<previous_tool_result><omitted /></previous_tool_result>"
                    f"\n</previous_tool_call>"
                )

        elif role == "tool":
            # 结果一律省略；来源工具与入参已由对应 tool_call 块承载，
            # 单独再输出省略行属重复，直接跳过
            continue

    folded = "\n".join(parts)
    est_tokens = len(folded) // CONTEXT_CHARS_PER_TOKEN if folded else 0
    return folded, est_tokens


def fold_to_summary_content(folded_text: str) -> str:
    """把折叠文本包装为可直接放入压缩产物的摘要正文。

    工程折叠路径不产出 9 段结构，标注其确定性来源与省略语义，
    让续写模型知道细节被省略、可回查转录。
    """
    return (
        "Summary of the earlier conversation (deterministic fold — "
        "tool outputs omitted, marked with <omitted />; "
        "full details are in the session transcript):\n\n"
        f"{folded_text}"
    )
