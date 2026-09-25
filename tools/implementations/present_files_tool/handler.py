"""present_files 实现：校验交付文件并产出结构化 metadata 供 loop 外发。

外发链路：handler 只负责校验与组装 metadata（type=present_files）；
query/loop.py 在工具结果转消息处检测该 metadata，额外 yield
{role:"present_files", files, explanation} 结构化事件给前端。
"""

from __future__ import annotations

from typing import Any

import os

from server.paths import project_root
from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.implementations.present_files_tool.schema import PresentFilesInput

# 单文件大小上限：超过视为不该整份打开的资源（如大二进制），直接拒绝
MAX_PRESENT_BYTES = 20_000_000


async def handle_present_files(inp: PresentFilesInput) -> dict[str, Any]:
    """校验文件列表并组装交付 metadata。

    返回字段：files（工作区相对路径，按给定顺序）、cwd、explanation。
    """
    if not inp.files:
        raise ToolExecutionError(
            "empty_files", "files 不能为空：至少给出一个要交付的文件路径"
        )

    root = project_root()
    rel_files: list[str] = []
    for raw in inp.files:
        if not raw or not raw.strip():
            raise ToolExecutionError(
                "invalid_path", f"文件路径为空：{raw!r}。每个条目都必须是非空路径"
            )
        path = resolve_workspace_path(raw.strip(), must_exist=True)
        if not path.is_file():
            raise ToolExecutionError(
                "not_a_file",
                f"不是文件（可能是目录）：{raw}。present_files 只交付文件，不交付目录",
            )
        if path.stat().st_size > MAX_PRESENT_BYTES:
            raise ToolExecutionError(
                "file_too_large",
                f"文件超过 {MAX_PRESENT_BYTES // 1_000_000}MB：{raw}。"
                "过大文件不适合直接打开，请交付精简后的产物",
            )
        rel = os.path.relpath(path, root).replace("\\", "/")
        rel_files.append(rel)

    return {
        "files": rel_files,
        "cwd": str(root),
        "explanation": (inp.explanation or "").strip(),
    }


def format_model_content(structured: dict[str, Any]) -> str:
    """把结构化结果拼成给模型的文本。"""
    listed = "\n".join(f"- {f}" for f in structured["files"])
    return (
        f"已向用户呈现 {len(structured['files'])} 个文件（第一个已自动聚焦打开）：\n"
        f"{listed}\n"
        "请在回复中简要说明交付内容与结论，不要复述文件全文。"
    )
