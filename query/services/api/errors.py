"""结构化 API 错误转换模块。

将 openai SDK 异常分类为统一的 APIError 结构，
提供错误恢复性判断和消息格式转换。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import openai


# ---------------------------------------------------------------------------
# APIError dataclass
# ---------------------------------------------------------------------------


@dataclass
class APIError:
    """结构化 API 错误。

    Attributes:
        type: 错误类型分类
            - "context_length_exceeded": 上下文长度超限
            - "rate_limit": 速率限制
            - "server_error": 服务端错误 (5xx)
            - "auth_error": 认证错误
            - "unknown": 未知错误
        message: 人类可读的错误消息
        status_code: HTTP 状态码（可能为 None）
        retry_after: 重试等待时间（秒），来自 retry-after header
    """

    type: str
    message: str
    status_code: int | None = None
    retry_after: float | None = None


# ---------------------------------------------------------------------------
# classify_error — 将异常分类为 APIError
# ---------------------------------------------------------------------------


def classify_error(error: Exception) -> APIError:
    """将 openai SDK 异常分类为 APIError。

    分类规则：
      - openai.BadRequestError + "context_length" → context_length_exceeded
      - openai.RateLimitError → rate_limit
      - openai.AuthenticationError → auth_error
      - openai.APIStatusError + 5xx → server_error
      - 其他 → unknown
    """
    # ---- BadRequestError + context_length ----
    if isinstance(error, openai.BadRequestError):
        msg = str(error).lower()
        if "context_length" in msg or "maximum context length" in msg:
            return APIError(
                type="context_length_exceeded",
                message=str(error),
                status_code=error.status_code if hasattr(error, "status_code") else 400,
            )
        return APIError(
            type="unknown",
            message=str(error),
            status_code=error.status_code if hasattr(error, "status_code") else None,
        )

    # ---- RateLimitError ----
    if isinstance(error, openai.RateLimitError):
        retry_after = _extract_retry_after(error)
        return APIError(
            type="rate_limit",
            message=str(error),
            status_code=error.status_code if hasattr(error, "status_code") else 429,
            retry_after=retry_after,
        )

    # ---- AuthenticationError ----
    if isinstance(error, openai.AuthenticationError):
        return APIError(
            type="auth_error",
            message=str(error),
            status_code=error.status_code if hasattr(error, "status_code") else 401,
        )

    # ---- APIStatusError + 5xx ----
    if isinstance(error, openai.APIStatusError):
        status_code = error.status_code if hasattr(error, "status_code") else None
        if status_code is not None and status_code >= 500:
            retry_after = _extract_retry_after(error)
            return APIError(
                type="server_error",
                message=str(error),
                status_code=status_code,
                retry_after=retry_after,
            )
        return APIError(
            type="unknown",
            message=str(error),
            status_code=status_code,
        )

    # ---- APIConnectionError (网络/超时) ----
    if isinstance(error, openai.APIConnectionError):
        return APIError(
            type="server_error",
            message=str(error),
            status_code=None,
        )

    # ---- 其他 ----
    return APIError(
        type="unknown",
        message=str(error),
        status_code=None,
    )


# ---------------------------------------------------------------------------
# get_assistant_message_from_error — 错误 → assistant 消息格式
# ---------------------------------------------------------------------------


def get_assistant_message_from_error(error: APIError) -> dict[str, Any]:
    """将 APIError 转换为 assistant 消息格式（用于追加到消息列表）。

    Returns:
        {"role": "assistant", "content": error_message}
    """
    return {
        "role": "assistant",
        "content": error.message,
    }


# ---------------------------------------------------------------------------
# is_recoverable_error — 判断错误是否可恢复
# ---------------------------------------------------------------------------


def is_recoverable_error(error: APIError) -> bool:
    """判断错误是否可恢复。

    可恢复错误：
      - context_length_exceeded → True（可通过压缩恢复）
      - rate_limit → True（可重试）
      - server_error → True（可重试）

    不可恢复错误：
      - auth_error → False
      - unknown → False
    """
    return error.type in {
        "context_length_exceeded",
        "rate_limit",
        "server_error",
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _extract_retry_after(error: openai.APIStatusError) -> float | None:
    """从错误响应 header 中提取 retry-after 值（秒）。"""
    try:
        headers = getattr(error, "headers", None)
        if headers is None:
            return None
        # headers 可能是 dict 或 httpx.Headers
        retry_after = None
        if isinstance(headers, dict):
            retry_after = headers.get("retry-after")
        else:
            retry_after = headers.get("retry-after")

        if retry_after is not None:
            return float(retry_after)
    except (ValueError, TypeError, AttributeError):
        pass
    return None
