"""查询模块 — Agentic 循环引擎。"""

from query.loop import query, query_loop, State
from query.config import QueryConfig, build_query_config
from query.deps import QueryDeps, production_deps
from query.stop_hooks import StopHookResult, run_stop_hooks
from query.token_budget import TokenBudget, estimate_tokens, is_over_budget, remaining

__all__ = [
    "query",
    "query_loop",
    "State",
    "QueryConfig",
    "build_query_config",
    "QueryDeps",
    "production_deps",
    "StopHookResult",
    "run_stop_hooks",
    "TokenBudget",
    "estimate_tokens",
    "is_over_budget",
    "remaining",
]
