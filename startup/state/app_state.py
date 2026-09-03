"""应用状态 - 持有会话级的 token 用量、模型、累计成本。

server 通过 AppStateProvider.get_state() 读写。其余历史状态字段已随 CLI 移除。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    """Token 用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # 累计「实际发送的输入 token 总量」（协议无关口径，由 usage 事件的
    # total_input_tokens 累加）。缓存命中率分母用它：OpenAI 兼容协议的
    # input_tokens 已含缓存、Anthropic 的不含，直接相加会翻倍
    total_input_tokens: int = 0
    # 最近一次请求的 prompt_tokens，反映当前上下文大小（覆盖，不累加）
    last_prompt_tokens: int = 0
    # 最近一次请求的 cache_creation_input_tokens，反映已缓存大小（覆盖，不累加）
    last_cache_creation: int = 0


@dataclass
class AppState:
    """应用状态。

    只保留 server 实际读写的字段：token 用量、模型名、累计成本。
    """

    token_usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None
    total_cost_usd: float = 0.0
    # 最近一次请求的上下文分类 token 估算（覆盖，不累加）。
    # 结构 {分类名: token 数, "total": 总数}，生成逻辑见
    # query/services/context_metrics.py
    context_breakdown: dict | None = None


class AppStateProvider:
    """持有 AppState 实例，提供 get_state 访问。"""

    def __init__(self, initial_state: AppState | None = None) -> None:
        self._state: AppState = initial_state or AppState()

    def get_state(self) -> AppState:
        return self._state
