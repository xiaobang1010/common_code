"""提示词模板加载器。

系统提示词段与工具使用说明统一收敛为 prompts/templates/ 下的 .j2 文件
（Jinja2 语法），代码里不再保留提示词常量：
- prompts/templates/system/<name>.j2   系统提示词段
- prompts/templates/tools/<key>.j2     工具完整使用说明（模型侧 description）

模板渲染发生在每轮构建请求时，渲染结果进入原分段/缓存机制，缓存口径不变。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    """Jinja2 环境：块标签吃掉换行、未定义变量报错（提示词里留空洞比报错更危险）。"""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )


def render_prompt(rel_path: str, **ctx: Any) -> str:
    """渲染 prompts/templates/ 下的指定模板。

    Args:
        rel_path: 相对 templates 目录的路径，如 "tools/read.j2"、"system/subagent-guidance.j2"
        **ctx: 模板变量（如 Agent 模板的 agents 清单）

    Returns:
        渲染后的提示词正文（保留末尾换行，由调用方决定是否 strip）
    """
    return _env().get_template(rel_path).render(**ctx)


def load_tool_prompt(tool_key: str, **ctx: Any) -> str:
    """加载工具的完整使用说明（tools/<key>.j2），供 build_tool(prompt=...) 使用。"""
    return render_prompt(f"tools/{tool_key}.j2", **ctx)
