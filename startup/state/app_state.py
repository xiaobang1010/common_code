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


class AppStateProvider:
    """持有 AppState 实例，提供 get_state 访问。"""

    def __init__(self, initial_state: AppState | None = None) -> None:
        self._state: AppState = initial_state or AppState()

    def get_state(self) -> AppState:
        return self._state
