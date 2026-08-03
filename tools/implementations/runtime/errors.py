"""统一工具错误定义。

所有内置工具的执行错误统一抛 ToolExecutionError，由 handler 内部捕获后
转为 ToolResult(is_error=True)，或交由执行管线兜底处理。
"""

from __future__ import annotations


class ToolExecutionError(Exception):
    """工具执行错误。

    Attributes:
        code: 机器可读的错误码（如 path_outside_workspace / file_not_found）
        message: 面向模型/用户的错误文案
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 常用错误构造函数
# ---------------------------------------------------------------------------

def path_outside_workspace_error(path: str, workspace_root: str) -> ToolExecutionError:
    """路径越出工作区边界。"""
    return ToolExecutionError(
        "path_outside_workspace",
        f"路径越出工作区边界，拒绝访问：{path}（工作区：{workspace_root}）",
    )


def file_not_found_error(path: str) -> ToolExecutionError:
    """文件不存在。"""
    return ToolExecutionError("file_not_found", f"文件不存在：{path}")


def not_a_file_error(path: str) -> ToolExecutionError:
    """路径不是文件。"""
    return ToolExecutionError("not_a_file", f"路径不是文件：{path}")


def not_a_directory_error(path: str) -> ToolExecutionError:
    """路径不是目录。"""
    return ToolExecutionError("not_a_directory", f"路径不是目录：{path}")


def file_too_large_error(path: str, max_bytes: int) -> ToolExecutionError:
    """文件超过大小上限。"""
    return ToolExecutionError(
        "file_too_large",
        f"文件过大（超过 {max_bytes} 字节上限），请使用 offset/limit 分段读取：{path}",
    )
