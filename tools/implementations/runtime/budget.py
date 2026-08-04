"""结果预算执行 — 按 ResultBudget 描述符统一截断工具输出。

执行管线在工具结果返回模型前调用 apply_result_budget，
替代各工具内部散落的硬编码截断。
"""

from __future__ import annotations

from tools.protocol import DIRECTION_TAIL, ResultBudget


def apply_result_budget(content: str, budget: ResultBudget) -> str:
    """按预算截断输出文本。

    Args:
        content: 工具原始输出
        budget: 结果预算描述符（max_model_chars + 保留方向）

    Returns:
        未超预算时原样返回；超预算时按方向截断并附带提示。
    """
    limit = budget.max_model_chars
    if limit <= 0 or len(content) <= limit:
        return content

    total = len(content)
    if budget.preview_direction == DIRECTION_TAIL:
        # 保留末尾（命令输出的关键信息通常在尾部）
        kept = content[-limit:]
        return f"...（输出过长，已截断开头 {total - limit} 字符，共 {total} 字符）\n{kept}"

    # 默认保留开头
    kept = content[:limit]
    return f"{kept}\n...（输出过长，已截断末尾 {total - limit} 字符，共 {total} 字符）"
