"""Edit 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

import asyncio
import os
import tempfile

from server.file_events import notify_file_changed
from server.paths import MAX_EDITABLE_BYTES
from tools.implementations.file_edit_tool.schema import FileEditInput
from tools.implementations.runtime.errors import (
    ToolExecutionError,
    file_modified_error,
    file_not_found_error,
    file_too_large_error,
)
from tools.implementations.runtime.file_baseline import record_baseline
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.protocol import ToolUseContext


def _validate_edit_input(inp: FileEditInput, file_path) -> None:
    """编辑前置校验：old==new 无意义、文件不存在且非创建场景。

    Raises:
        ToolExecutionError: 校验失败
    """
    if inp.old_string == inp.new_string:
        raise ToolExecutionError(
            "no_change", "old_string 和 new_string 完全相同，无需修改"
        )
    if not file_path.exists() and inp.old_string != "":
        raise file_not_found_error(inp.file_path)


async def handle_edit(inp: FileEditInput, context: ToolUseContext) -> dict:
    """读取文件 → 搜索 old_string → 替换为 new_string → 写回。

    Returns:
        结构化结果字典：
        {
            "file_path": 绝对路径,
            "replacements": 实际替换次数,
            "added_lines": 新增行数, "removed_lines": 删除行数,
        }

    Raises:
        ToolExecutionError: 路径越界 / 无变更 / 文件不存在 / 未找到匹配 / 匹配不唯一
    """
    # 读写盘丢线程池执行，大文件编辑时不阻塞事件循环
    result = await asyncio.to_thread(_edit_sync, inp)

    # 写盘成功后刷新基线登记（供本会话内后续覆盖写免 Read），再广播文件变更
    # 事件（asyncio.Queue 非线程安全，广播留在事件循环上）
    record_baseline(result["file_path"], result["mtime"], result["size"])
    mtime = result.pop("mtime")
    size = result.pop("size")
    notify_file_changed(result["file_path"], "edit", mtime, size)
    return result


def _edit_sync(inp: FileEditInput) -> dict:
    """同步编辑内核：由 handle_edit 放入线程池执行。"""
    # 路径沙箱：解析并校验工作区边界
    file_path = resolve_workspace_path(inp.file_path)
    _validate_edit_input(inp, file_path)

    # 读取现有内容（新文件创建场景为空串）
    if file_path.exists():
        content = file_path.read_text(encoding="utf-8", errors="replace")
    else:
        content = ""

    # 搜索与替换
    if inp.old_string == "" and content == "":
        # 空文件/新文件 → 空字符串替换为 new_string
        updated = inp.new_string
        replacements = 1
    else:
        count = content.count(inp.old_string)
        if count == 0:
            raise ToolExecutionError(
                "string_not_found",
                f"未找到要替换的字符串。\n字符串：{inp.old_string}",
            )
        if count > 1 and not inp.replace_all:
            raise ToolExecutionError(
                "multiple_matches",
                f"找到 {count} 处匹配，但 replace_all=False。"
                f"请提供更多上下文或设置 replace_all=True。\n字符串：{inp.old_string}",
            )
        if inp.replace_all:
            updated = content.replace(inp.old_string, inp.new_string)
            replacements = count
        else:
            updated = content.replace(inp.old_string, inp.new_string, 1)
            replacements = 1

    # 写回前一致性校验（携带基线时）：防止覆盖自模型上次 Read 之后他人的改动
    if file_path.exists() and (inp.base_mtime is not None or inp.base_size is not None):
        st = file_path.stat()
        mtime_changed = inp.base_mtime is not None and int(st.st_mtime) != inp.base_mtime
        size_changed = inp.base_size is not None and st.st_size != inp.base_size
        if mtime_changed or size_changed:
            raise file_modified_error(inp.file_path)

    # 大小上限护栏
    size = len(updated.encode("utf-8"))
    if size > MAX_EDITABLE_BYTES:
        raise file_too_large_error(inp.file_path, MAX_EDITABLE_BYTES)

    # 原子写回：临时文件建在目标同目录，替换前恢复原文件权限，
    # 中断或失败时不留半截文件
    file_path.parent.mkdir(parents=True, exist_ok=True)
    existed = file_path.exists()
    orig_mode = file_path.stat().st_mode if existed else None
    fd, tmp_path = tempfile.mkstemp(
        dir=file_path.parent, prefix=".cc-edit-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(updated)
        if existed:
            os.chmod(tmp_path, orig_mode)
        os.replace(tmp_path, file_path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # 变更统计（增删行数）。size 取写盘后的真实字节数：
    # 文本模式写盘会做 \n→\r\n 转换，预计算的 encode 长度与磁盘不一致，
    # 会污染基线登记（后续覆盖写被误判 file_modified）
    removed = len(content.splitlines())
    added = len(updated.splitlines())
    st = file_path.stat()
    return {
        "file_path": str(file_path),
        "replacements": replacements,
        "added_lines": max(0, added - removed),
        "removed_lines": max(0, removed - added),
        "mtime": int(st.st_mtime),
        "size": st.st_size,
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。行数统计同时供前端事件行展示。"""
    replacements = structured.get("replacements", 1)
    added = structured.get("added_lines", 0)
    removed = structured.get("removed_lines", 0)
    return (
        f"文件 {structured['file_path']} 已成功更新"
        f"（替换 {replacements} 处，+{added} -{removed} 行）。"
    )
