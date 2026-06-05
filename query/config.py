"""查询配置 — 不可变配置快照。

参考原始 TypeScript 实现 src/query/config.ts。

在 query() 入口处一次性快照运行时配置，
与每次迭代可变的 State 分离，便于未来 step() 提取为纯 reducer。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# QueryConfig — 不可变配置快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryConfig:
    """查询配置快照。

    在 query() 入口处构建一次，迭代期间不可变。

    Attributes:
        model: 模型名称
        max_tokens: 最大输出 token
        temperature: 采样温度
        context_collapse_enabled: 是否启用上下文折叠
        auto_compact_enabled: 是否启用自动压缩
        permission_mode: 权限模式 — "default" | "plan" | "auto" | "bypass"
        tools: 可用工具列表
        system_prompt_sections: 系统提示词段列表
    """

    model: str = "gpt-4o"
    max_tokens: int = 8192
    temperature: float = 1.0
    context_collapse_enabled: bool = False
    auto_compact_enabled: bool = True
    permission_mode: str = "default"
    tools: list[Any] = field(default_factory=list)
    system_prompt_sections: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# build_query_config — 构建配置快照
# ---------------------------------------------------------------------------


def build_query_config(**overrides: Any) -> QueryConfig:
    """构建查询配置快照。

    从配置/环境变量读取默认值，overrides 可覆盖任何字段。

    环境变量映射：
      - COMMON_CODE_MODEL → model
      - COMMON_CODE_MAX_TOKENS → max_tokens
      - COMMON_CODE_TEMPERATURE → temperature
      - COMMON_CODE_CONTEXT_COLLAPSE → context_collapse_enabled
      - COMMON_CODE_DISABLE_AUTO_COMPACT → auto_compact_enabled（取反）
      - COMMON_CODE_PERMISSION_MODE → permission_mode

    Args:
        **overrides: 覆盖字段值

    Returns:
        QueryConfig 不可变配置快照
    """
    defaults: dict[str, Any] = {
        "model": os.environ.get("COMMON_CODE_MODEL", "gpt-4o"),
        "max_tokens": int(os.environ.get("COMMON_CODE_MAX_TOKENS", "8192")),
        "temperature": float(os.environ.get("COMMON_CODE_TEMPERATURE", "1.0")),
        "context_collapse_enabled": _is_env_truthy(
            os.environ.get("COMMON_CODE_CONTEXT_COLLAPSE", "")
        ),
        "auto_compact_enabled": not _is_env_truthy(
            os.environ.get("COMMON_CODE_DISABLE_AUTO_COMPACT", "")
        ),
        "permission_mode": os.environ.get(
            "COMMON_CODE_PERMISSION_MODE", "default"
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

    # ---- 测试 1: 默认配置 ----
    print("\n--- 测试 1: 默认配置 ---")
    config = build_query_config()
    assert config.model == os.environ.get("COMMON_CODE_MODEL", "gpt-4o")
    assert config.max_tokens == int(os.environ.get("COMMON_CODE_MAX_TOKENS", "8192"))
    assert config.temperature == float(os.environ.get("COMMON_CODE_TEMPERATURE", "1.0"))
    assert config.permission_mode == os.environ.get("COMMON_CODE_PERMISSION_MODE", "default")
    print(f"  model={config.model}, max_tokens={config.max_tokens}, "
          f"temperature={config.temperature}, permission_mode={config.permission_mode}")
    print("  [PASS] 默认配置")

    # ---- 测试 2: overrides 覆盖 ----
    print("\n--- 测试 2: overrides 覆盖 ---")
    config = build_query_config(model="claude-3-5-sonnet", max_tokens=4096, temperature=0.5)
    assert config.model == "claude-3-5-sonnet"
    assert config.max_tokens == 4096
    assert config.temperature == 0.5
    print(f"  model={config.model}, max_tokens={config.max_tokens}, temperature={config.temperature}")
    print("  [PASS] overrides 覆盖")

    # ---- 测试 3: frozen 不可变 ----
    print("\n--- 测试 3: frozen 不可变 ---")
    config = build_query_config()
    try:
        config.model = "other"  # type: ignore
        assert False, "frozen dataclass 不应允许修改"
    except AttributeError:
        print("  frozen dataclass 修改被拒绝: OK")
    print("  [PASS] frozen 不可变")

    # ---- 测试 4: permission_mode 有效值 ----
    print("\n--- 测试 4: permission_mode 有效值 ---")
    for mode in ("default", "plan", "auto", "bypass"):
        config = build_query_config(permission_mode=mode)
        assert config.permission_mode == mode
        print(f"  {mode}: OK")
    print("  [PASS] permission_mode 有效值")

    # ---- 测试 5: tools 和 system_prompt_sections ----
    print("\n--- 测试 5: tools 和 system_prompt_sections ---")
    config = build_query_config(tools=["tool1", "tool2"], system_prompt_sections=[{"name": "test"}])
    assert config.tools == ["tool1", "tool2"]
    assert config.system_prompt_sections == [{"name": "test"}]
    print(f"  tools={config.tools}, sections={config.system_prompt_sections}")
    print("  [PASS] tools 和 system_prompt_sections")

    # ---- 测试 6: auto_compact_enabled 默认值 ----
    print("\n--- 测试 6: auto_compact_enabled 默认值 ---")
    config = build_query_config()
    assert isinstance(config.auto_compact_enabled, bool)
    print(f"  auto_compact_enabled={config.auto_compact_enabled}")
    config_disabled = build_query_config(auto_compact_enabled=False)
    assert config_disabled.auto_compact_enabled is False
    print("  [PASS] auto_compact_enabled 默认值")

    # ---- 测试 7: context_collapse_enabled ----
    print("\n--- 测试 7: context_collapse_enabled ---")
    config = build_query_config(context_collapse_enabled=True)
    assert config.context_collapse_enabled is True
    config = build_query_config(context_collapse_enabled=False)
    assert config.context_collapse_enabled is False
    print("  [PASS] context_collapse_enabled")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
