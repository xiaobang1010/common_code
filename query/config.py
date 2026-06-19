"""查询配置 — 循环级快照。

查询配置 — 循环级快照。

QueryConfig 只保留每次 query 循环级别的快照字段，
会话级别的配置（model、max_tokens、temperature、permission_mode、tools、
system_prompt_sections）已迁移到 QueryEngineConfig 中。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# QueryConfig — 循环级配置快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryConfig:
    """循环级配置快照。

    每次 submitMessage 时构建一次，循环期间不可变。
    会话级配置见 QueryEngineConfig。

    Attributes:
        session_id: 会话标识，每次 submitMessage 时生成
        auto_compact_enabled: 是否启用自动压缩，从环境变量快照
        context_collapse_enabled: 是否启用上下文折叠，从环境变量快照
    """

    session_id: str = ""
    auto_compact_enabled: bool = True
    context_collapse_enabled: bool = False


# ---------------------------------------------------------------------------
# build_query_config — 构建循环级配置快照
# ---------------------------------------------------------------------------


def build_query_config(session_id: str = "", **overrides: Any) -> QueryConfig:
    """构建循环级配置快照。

    从环境变量快照 auto_compact_enabled 和 context_collapse_enabled，
    session_id 默认空字符串，可被 overrides 覆盖。

    环境变量映射：
      - COMMON_CODE_DISABLE_AUTO_COMPACT → auto_compact_enabled（取反）
      - COMMON_CODE_CONTEXT_COLLAPSE → context_collapse_enabled

    Args:
        session_id: 会话标识，默认空字符串
        **overrides: 覆盖字段值

    Returns:
        QueryConfig 不可变循环级配置快照
    """
    defaults: dict[str, Any] = {
        "session_id": session_id,
        "auto_compact_enabled": not _is_env_truthy(
            os.environ.get("COMMON_CODE_DISABLE_AUTO_COMPACT", "")
        ),
        "context_collapse_enabled": _is_env_truthy(
            os.environ.get("COMMON_CODE_CONTEXT_COLLAPSE", "")
        ),
    }

    # overrides 覆盖默认值
    defaults.update(overrides)

    return QueryConfig(**defaults)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _is_env_truthy(value: str) -> bool:
    """判断环境变量值是否为真值。"""
    return value.lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("查询配置测试")
    print("=" * 60)

    # ---- 测试 1: 默认 3 个字段 ----
    print("\n--- 测试 1: 默认 3 个字段 ---")
    config = build_query_config()
    assert config.session_id == ""
    assert config.auto_compact_enabled is not _is_env_truthy(
        os.environ.get("COMMON_CODE_DISABLE_AUTO_COMPACT", "")
    )
    assert config.context_collapse_enabled == _is_env_truthy(
        os.environ.get("COMMON_CODE_CONTEXT_COLLAPSE", "")
    )
    print(f"  session_id={config.session_id!r}, "
          f"auto_compact_enabled={config.auto_compact_enabled}, "
          f"context_collapse_enabled={config.context_collapse_enabled}")
    print("  [PASS] 默认 3 个字段")

    # ---- 测试 2: overrides 覆盖 session_id 和开关 ----
    print("\n--- 测试 2: overrides 覆盖 session_id 和开关 ---")
    config = build_query_config(
        session_id="sess-123",
        auto_compact_enabled=False,
        context_collapse_enabled=True,
    )
    assert config.session_id == "sess-123"
    assert config.auto_compact_enabled is False
    assert config.context_collapse_enabled is True
    print(f"  session_id={config.session_id!r}, "
          f"auto_compact_enabled={config.auto_compact_enabled}, "
          f"context_collapse_enabled={config.context_collapse_enabled}")
    print("  [PASS] overrides 覆盖 session_id 和开关")

    # ---- 测试 3: frozen 不可变 ----
    print("\n--- 测试 3: frozen 不可变 ---")
    config = build_query_config()
    try:
        config.session_id = "other"  # type: ignore
        assert False, "frozen dataclass 不应允许修改"
    except AttributeError:
        print("  frozen dataclass 修改被拒绝: OK")
    print("  [PASS] frozen 不可变")

    # ---- 测试 4: auto_compact_enabled 默认值 ----
    print("\n--- 测试 4: auto_compact_enabled 默认值 ---")
    config = build_query_config()
    assert isinstance(config.auto_compact_enabled, bool)
    print(f"  auto_compact_enabled={config.auto_compact_enabled}")
    config_disabled = build_query_config(auto_compact_enabled=False)
    assert config_disabled.auto_compact_enabled is False
    print("  [PASS] auto_compact_enabled 默认值")

    # ---- 测试 5: context_collapse_enabled ----
    print("\n--- 测试 5: context_collapse_enabled ---")
    config = build_query_config(context_collapse_enabled=True)
    assert config.context_collapse_enabled is True
    config = build_query_config(context_collapse_enabled=False)
    assert config.context_collapse_enabled is False
    print("  [PASS] context_collapse_enabled")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
