"""模型配置定义与查询。

参考原始 TypeScript 实现 src/utils/model/configs.ts 和 modelCapabilities.ts。

提供内置模型配置和查询接口。
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# ModelConfig dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelConfig:
    """模型配置。"""

    name: str
    context_window: int  # 上下文窗口大小（token）
    max_output_tokens: int  # 最大输出 token
    supports_streaming: bool = True  # 是否支持流式
    supports_tools: bool = True  # 是否支持工具调用
    supports_vision: bool = False  # 是否支持视觉


# ---------------------------------------------------------------------------
# 内置模型配置
# ---------------------------------------------------------------------------

_BUILTIN_MODELS: dict[str, ModelConfig] = {}

# 默认模型配置
_DEFAULT_MODEL_CONFIG = ModelConfig(
    name="default",
    context_window=200000,
    max_output_tokens=32768,
    supports_streaming=True,
    supports_tools=True,
    supports_vision=False,
)


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------


def get_model_config(model: str) -> ModelConfig:
    """根据模型名返回配置。

    查找策略：
      1. 精确匹配内置配置
      2. 前缀匹配（模型名以已知 key 开头）
      3. 返回默认配置

    Args:
        model: 模型名称，如 "gpt-4o", "claude-3-5-sonnet"

    Returns:
        对应的 ModelConfig，未找到则返回默认配置
    """
    if not model:
        return _DEFAULT_MODEL_CONFIG

    # 精确匹配
    if model in _BUILTIN_MODELS:
        return _BUILTIN_MODELS[model]

    # 前缀匹配：例如 "gpt-4o-2024-05-13" 匹配 "gpt-4o"
    model_lower = model.lower()
    for key, config in _BUILTIN_MODELS.items():
        if model_lower.startswith(key.lower()):
            return config

    return _DEFAULT_MODEL_CONFIG


def get_effective_context_window(model: str) -> int:
    """获取有效上下文窗口大小。

    Args:
        model: 模型名称

    Returns:
        上下文窗口大小（token 数）
    """
    config = get_model_config(model)
    return config.context_window


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("模型配置测试")
    print("=" * 60)

    # 测试 1: 默认配置
    print("\n--- 测试 1: 默认配置 ---")
    cfg = get_model_config("unknown-model")
    assert cfg.name == "default", f"未知模型应返回 default, got {cfg.name}"
    assert cfg.context_window == 200000
    assert cfg.max_output_tokens == 32768
    assert cfg.supports_vision is False
    print(f"  unknown-model: context_window={cfg.context_window}, max_output={cfg.max_output_tokens}")

    cfg = get_model_config("")
    assert cfg.name == "default"
    print(f"  空字符串: context_window={cfg.context_window}")
    print("  [PASS] 默认配置")

    # 测试 2: get_effective_context_window
    print("\n--- 测试 2: get_effective_context_window ---")
    assert get_effective_context_window("unknown") == 200000
    assert get_effective_context_window("") == 200000
    print("  unknown: 200000")
    print("  empty: 200000")
    print("  [PASS] get_effective_context_window")

    # 测试 3: frozen
    print("\n--- 测试 3: frozen dataclass ---")
    cfg = get_model_config("unknown")
    try:
        cfg.context_window = 999999  # type: ignore
        assert False, "frozen dataclass 不应允许修改"
    except AttributeError:
        print("  frozen dataclass 修改被拒绝: OK")
    print("  [PASS] frozen dataclass")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
