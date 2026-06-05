"""输入验证工具 — 使用 Pydantic schema 验证工具输入。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from tools.protocol import Tool


def validate_tool_input(
    tool: Tool,
    raw_input: dict,
) -> tuple[BaseModel | None, str | None]:
    """验证工具输入。

    使用 tool.input_schema.model_validate() 验证输入。

    返回：
        (validated_model, None) — 验证成功，返回 Pydantic 模型实例
        (None, error_message) — 验证失败
    """
    try:
        validated = tool.input_schema.model_validate(raw_input)
        return validated, None
    except ValidationError as e:
        return None, str(e)
