"""工具注册表 — 参考原始 tools/ts。"""

from __future__ import annotations

from tools.protocol import Tool
from tools.implementations.bash_tool import get_bash_tool
from tools.implementations.file_read_tool import get_file_read_tool
from tools.implementations.file_edit_tool import get_file_edit_tool
from tools.implementations.file_write_tool import get_file_write_tool
from tools.implementations.glob_tool import get_glob_tool
from tools.implementations.grep_tool import get_grep_tool


def get_tools() -> list[Tool]:
    """返回所有内置工具列表。"""
    return [
        get_bash_tool(),
        get_file_read_tool(),
        get_file_edit_tool(),
        get_file_write_tool(),
        get_glob_tool(),
        get_grep_tool(),
    ]
