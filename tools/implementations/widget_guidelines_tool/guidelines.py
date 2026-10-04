"""可视化设计规范正文（与交付工具正文同源，逐字保留，不做二次加工）。

正文按段存放在同目录 guidelines/*.md，本模块只负责按模块集合组装，
组装顺序与规则来自既定实现，禁止随意调整。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# 模块全集与非法值过滤依据
ALL_MODULES = {"diagram", "mockup", "interactive", "art", "chart"}

_GUIDELINES_DIR = Path(__file__).parent / "guidelines"


@lru_cache(maxsize=None)
def _section(name: str) -> str:
    path = _GUIDELINES_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def normalize_modules(modules: list[str] | None) -> list[str]:
    """按既定规则归一化模块列表：仅保留合法值，去重并保持首次出现顺序。"""
    results: list[str] = []
    seen: set[str] = set()
    for raw in modules or []:
        if not raw or not isinstance(raw, str):
            continue
        value = raw.strip()
        if value not in ALL_MODULES or value in seen:
            continue
        seen.add(value)
        results.append(value)
    return results


def build_guidelines(modules: list[str] | None = None) -> str:
    """按模块集合组装设计规范全文。

    公共段（设计系统/调色板）始终包含；SVG 设置与 UI 组件按模块触发；
    各模块专属段按用户给定顺序追加（mockup 与 interactive 同时给出时，
    交互指导段会按既定行为出现两次——保持与来源实现一致）。
    """
    normalized = normalize_modules(modules)
    sections = [_section("core-design-system"), _section("color-palette")]
    if any(mod in ("diagram", "art") for mod in normalized):
        sections.append(_section("svg-setup"))
    if any(mod in ("mockup", "interactive", "chart") for mod in normalized):
        sections.append(_section("ui-components"))
    for mod in normalized:
        if mod == "diagram":
            sections.append(_section("diagram-types"))
        elif mod in ("mockup", "interactive"):
            sections.append(_section("interactive-guidance"))
        elif mod == "chart":
            sections.append(_section("chartjs-rules"))
            sections.append(_section("d3-choropleth"))
        elif mod == "art":
            sections.append(_section("art-guidance"))
    return "\n\n".join(sections)


def parse_modules(raw: object) -> list[str]:
    """解析 modules 入参：数组、JSON 编码字符串数组、逗号/空白分隔列表皆可。"""
    if isinstance(raw, list):
        return [item.strip() for item in (str(x) for x in raw) if item.strip()]
    if not isinstance(raw, str):
        return []
    trimmed = raw.strip()
    if not trimmed:
        return []
    if trimmed.startswith("["):
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, list):
                return [
                    item.strip()
                    for item in (str(x) for x in parsed)
                    if item.strip()
                ]
        except Exception:  # noqa: BLE001 非 JSON 字符串按分隔列表处理
            pass
    return [item for item in (s.strip() for s in re.split(r"[\s,]+", trimmed)) if item]
