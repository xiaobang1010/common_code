"""Token 预算管理 — 跟踪和控制 token 使用量。

参考原始 TypeScript 实现 src/query/tokenBudget.ts。

提供 token 预算跟踪、超预算检测和消息 token 估算。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# TokenBudget — Token 预算
# ---------------------------------------------------------------------------


@dataclass
class TokenBudget:
    """Token 预算跟踪。

    Attributes:
        used: 已使用 token
        total: 总 token 预算
        reserved: 预留 token（用于输出）
    """

    used: int = 0
    total: int = 128000
    reserved: int = 8192


# ---------------------------------------------------------------------------
# remaining — 剩余 token
# ---------------------------------------------------------------------------


def remaining(budget: TokenBudget) -> int:
    """计算剩余可用 token。

    剩余 = total - used - reserved

    Args:
        budget: Token 预算

    Returns:
        剩余 token 数（最小为 0）
    """
    return max(0, budget.total - budget.used - budget.reserved)


# ---------------------------------------------------------------------------
# is_over_budget — 是否超预算
# ---------------------------------------------------------------------------


def is_over_budget(budget: TokenBudget) -> bool:
    """判断是否超出 token 预算。

    当 used + reserved >= total 时认为超预算。

    Args:
        budget: Token 预算

    Returns:
        是否超预算
    """
    return budget.used + budget.reserved >= budget.total


# ---------------------------------------------------------------------------
# estimate_tokens — 估算消息 token 数
# ---------------------------------------------------------------------------


# 每 4 个字符约 1 个 token（粗略估算）
_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict]) -> int:
    """估算消息列表的 token 数。

    简单估算：将消息序列化为 JSON，每 4 个字符约 1 个 token。
    包含消息格式的额外开销（每条消息约 4 token）。

    Args:
        messages: 消息列表

    Returns:
        估算的 token 数（最小为 0）
    """
    if not messages:
        return 0

    total_chars = 0
    for msg in messages:
        # 消息格式开销
        total_chars += 4 * _CHARS_PER_TOKEN  # role + 格式开销
        # 内容
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # 多模态内容块
            for block in content:
                if isinstance(block, dict):
                    block_text = block.get("text", "")
                    if isinstance(block_text, str):
                        total_chars += len(block_text)
        # tool_calls
        tool_calls = msg.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            for tc in tool_calls:
                total_chars += len(json.dumps(tc, ensure_ascii=False))

    return max(0, total_chars // _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Token 预算管理测试")
    print("=" * 60)

    # ---- 测试 1: TokenBudget 创建 ----
    print("\n--- 测试 1: TokenBudget 创建 ---")
    budget = TokenBudget(used=1000, total=128000, reserved=8192)
    assert budget.used == 1000
    assert budget.total == 128000
    assert budget.reserved == 8192
    print(f"  used={budget.used}, total={budget.total}, reserved={budget.reserved}")
    print("  [PASS] TokenBudget 创建")

    # ---- 测试 2: remaining 计算 ----
    print("\n--- 测试 2: remaining 计算 ---")
    budget = TokenBudget(used=100000, total=128000, reserved=8192)
    r = remaining(budget)
    assert r == 128000 - 100000 - 8192
    print(f"  remaining={r}")
    # 边界：used 超过 total - reserved
    budget2 = TokenBudget(used=200000, total=128000, reserved=8192)
    r2 = remaining(budget2)
    assert r2 == 0
    print(f"  over-budget remaining={r2}")
    print("  [PASS] remaining 计算")

    # ---- 测试 3: is_over_budget ----
    print("\n--- 测试 3: is_over_budget ---")
    budget_ok = TokenBudget(used=100000, total=128000, reserved=8192)
    assert is_over_budget(budget_ok) is False
    print(f"  used=100000, total=128000, reserved=8192 → over={is_over_budget(budget_ok)}")

    budget_over = TokenBudget(used=120000, total=128000, reserved=8192)
    assert is_over_budget(budget_over) is True
    print(f"  used=120000, total=128000, reserved=8192 → over={is_over_budget(budget_over)}")

    budget_exact = TokenBudget(used=119808, total=128000, reserved=8192)
    assert is_over_budget(budget_exact) is True  # used + reserved == total
    print(f"  used=119808, total=128000, reserved=8192 → over={is_over_budget(budget_exact)}")
    print("  [PASS] is_over_budget")

    # ---- 测试 4: estimate_tokens — 简单消息 ----
    print("\n--- 测试 4: estimate_tokens — 简单消息 ---")
    messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"},
    ]
    tokens = estimate_tokens(messages)
    assert tokens > 0
    print(f"  {len(messages)} 条消息 → ~{tokens} tokens")
    print("  [PASS] estimate_tokens — 简单消息")

    # ---- 测试 5: estimate_tokens — 空列表 ----
    print("\n--- 测试 5: estimate_tokens — 空列表 ---")
    tokens = estimate_tokens([])
    assert tokens == 0
    print(f"  空列表 → {tokens} tokens")
    print("  [PASS] estimate_tokens — 空列表")

    # ---- 测试 6: estimate_tokens — 含 tool_calls ----
    print("\n--- 测试 6: estimate_tokens — 含 tool_calls ---")
    messages = [
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "/tmp/test.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_001", "content": "file contents here"},
    ]
    tokens = estimate_tokens(messages)
    assert tokens > 0
    print(f"  含 tool_calls 的消息 → ~{tokens} tokens")
    print("  [PASS] estimate_tokens — 含 tool_calls")

    # ---- 测试 7: estimate_tokens — 多模态内容 ----
    print("\n--- 测试 7: estimate_tokens — 多模态内容 ---")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
        },
    ]
    tokens = estimate_tokens(messages)
    assert tokens > 0
    print(f"  多模态消息 → ~{tokens} tokens")
    print("  [PASS] estimate_tokens — 多模态内容")

    # ---- 测试 8: 默认值 ----
    print("\n--- 测试 8: 默认值 ---")
    budget = TokenBudget()
    assert budget.used == 0
    assert budget.total == 128000
    assert budget.reserved == 8192
    assert remaining(budget) == 128000 - 0 - 8192
    assert is_over_budget(budget) is False
    print(f"  默认: used={budget.used}, total={budget.total}, reserved={budget.reserved}")
    print("  [PASS] 默认值")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
