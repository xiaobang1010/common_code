"""推理参数映射的校验与求值（纯函数，便于测试）。

映射支持两种形态，判定规则与配置路由校验共用本模块：
  1. 按等级形态：顶层键集合与已配置等级集合完全相等且所有值均为对象，
     选中等级时直接取对应对象作为参数补丁；
  2. 模板形态：不满足按等级形态的 JSON 对象，字符串值中仅允许
     {reasoningLevel} 占位符，求值时以所选等级替换。

参数补丁以 JSON 深合并方式并入请求体：值为 null 的键表示删除该键。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

# 模板形态中唯一合法的占位符
REASONING_LEVEL_PLACEHOLDER = "{reasoningLevel}"

# 匹配字符串里出现的任意 {xxx} 记号，用于校验未知占位符
_BRACE_TOKEN_RE = re.compile(r"\{[^{}]*\}")


def parse_params_map(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """把映射字符串解析为 JSON 对象。

    Returns:
        (对象, 错误信息)；raw 为空串表示未配置，返回 (None, None)
    """
    if not raw or not raw.strip():
        return None, None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"推理参数映射不是合法 JSON: {e}"
    if not isinstance(obj, dict) or not obj:
        return None, "推理参数映射必须是非空 JSON 对象"
    return obj, None


def is_per_level_form(obj: dict[str, Any], levels: list[str]) -> bool:
    """按等级形态判定：顶层键集合与等级集合完全相等且所有值均为对象。"""
    return set(obj.keys()) == set(levels) and all(
        isinstance(v, dict) for v in obj.values()
    )


def _find_unknown_placeholders(value: Any) -> str | None:
    """递归扫描模板形态中的字符串值，返回首个未知占位符（无则 None）。"""
    if isinstance(value, str):
        for token in _BRACE_TOKEN_RE.findall(value):
            if token != REASONING_LEVEL_PLACEHOLDER:
                return token
        return None
    if isinstance(value, dict):
        for v in value.values():
            found = _find_unknown_placeholders(v)
            if found:
                return found
        return None
    if isinstance(value, list):
        for v in value:
            found = _find_unknown_placeholders(v)
            if found:
                return found
    return None


def validate_params_map(raw: str, levels: list[str]) -> str | None:
    """校验映射配置，返回错误信息；None 表示合法或未配置。"""
    obj, err = parse_params_map(raw)
    if err:
        return err
    if obj is None:
        return None
    # 命中按等级形态即合法；否则按模板形态校验占位符
    if is_per_level_form(obj, levels):
        return None
    unknown = _find_unknown_placeholders(obj)
    if unknown:
        return f"模板形态仅允许 {REASONING_LEVEL_PLACEHOLDER} 占位符，发现未知占位符 {unknown}"
    return None


def _substitute(value: Any, level: str) -> Any:
    """递归替换模板字符串中的 {reasoningLevel} 占位符。"""
    if isinstance(value, str):
        return value.replace(REASONING_LEVEL_PLACEHOLDER, level)
    if isinstance(value, dict):
        return {k: _substitute(v, level) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, level) for v in value]
    return value


def resolve_reasoning_params(
    raw_map: str, levels: list[str], level: str
) -> dict[str, Any] | None:
    """按所选等级求值映射，输出待合并的参数补丁；无结果返回 None。"""
    if not level:
        return None
    obj, err = parse_params_map(raw_map)
    if err or obj is None:
        return None
    if is_per_level_form(obj, levels):
        patch = obj.get(level)
        return copy.deepcopy(patch) if isinstance(patch, dict) else None
    # 模板形态：仅当所选等级在列表内才生效（调用方已校验合法性）
    if level not in levels:
        return None
    substituted = _substitute(obj, level)
    return substituted if isinstance(substituted, dict) else None


def resolve_reasoning_patch(model: str, level: str) -> dict[str, Any] | None:
    """按活动模型配置（get_model_config 单源）求值所选等级的参数补丁。"""
    if not level:
        return None
    from startup.model.config import get_model_config

    config = get_model_config(model)
    return resolve_reasoning_params(
        config.reasoning_params_map, list(config.reasoning_levels), level
    )


def deep_merge_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """把补丁深合并进 base（原地修改）：值为 None 表示删除对应键。"""
    for key, value in patch.items():
        if value is None:
            base.pop(key, None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge_patch(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base
