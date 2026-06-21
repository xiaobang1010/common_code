"""模型定价表和 token 到 USD 换算。

维护常见模型的单价（USD per token），并提供 calculate_cost 把
单次请求的 usage 字典换算成美元成本。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 定价表：价格单位是 USD per token
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {
        "input": 3e-6,
        "output": 15e-6,
        "cache_read": 0.3e-6,
        "cache_creation": 3.75e-6,
    },
    "claude-opus-4-20250514": {
        "input": 15e-6,
        "output": 75e-6,
        "cache_read": 1.5e-6,
        "cache_creation": 18.75e-6,
    },
    "claude-3-5-sonnet-20241022": {
        "input": 3e-6,
        "output": 15e-6,
        "cache_read": 0.3e-6,
        "cache_creation": 3.75e-6,
    },
    "claude-3-5-haiku-20241022": {
        "input": 0.8e-6,
        "output": 4e-6,
        "cache_read": 0.08e-6,
        "cache_creation": 1e-6,
    },
    "gpt-4o": {"input": 2.5e-6, "output": 10e-6},
    "gpt-4o-mini": {"input": 0.15e-6, "output": 0.6e-6},
}


def _match_pricing(model: str) -> dict[str, float] | None:
    """模糊匹配模型名到定价表。

    传入的 model 名可能带版本后缀，先精确匹配，再用 startswith / in 匹配。
    """
    # 精确匹配
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # 模糊匹配：定价表 key 是传入 model 的前缀
    for key, pricing in MODEL_PRICING.items():
        if model.startswith(key) or key in model:
            return pricing
    return None


def calculate_cost(model: str, usage: dict) -> float:
    """根据模型定价和 usage 字典算出本次请求的美元成本。

    usage 的 key 可能是：
      - prompt_tokens / completion_tokens / total_tokens（OpenAI 风格）
      - cache_read_input_tokens / cache_creation_input_tokens（Claude 风格，可选）

    模型不在定价表中返回 0.0 并打 warning。
    """
    pricing = _match_pricing(model)
    if pricing is None:
        logger.warning(f"模型 {model} 不在定价表中，成本按 0 计算")
        return 0.0

    # 输入 token：优先 prompt_tokens，兼容 input_tokens
    input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)

    cost = (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + cache_read * pricing.get("cache_read", 0.0)
        + cache_creation * pricing.get("cache_creation", 0.0)
    )
    return cost
