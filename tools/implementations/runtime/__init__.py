"""内置工具运行时支撑层 — 依赖注册。"""

from tools.implementations.runtime.errors import (
    ToolExecutionError,
    file_not_found_error,
    file_too_large_error,
    not_a_directory_error,
    not_a_file_error,
    path_outside_workspace_error,
)
from tools.implementations.runtime.paths import (
    get_workspace_root,
    resolve_workspace_path,
)
from tools.implementations.runtime.budget import apply_result_budget

__all__ = [
    "ToolExecutionError",
    "file_not_found_error",
    "file_too_large_error",
    "not_a_directory_error",
    "not_a_file_error",
    "path_outside_workspace_error",
    "get_workspace_root",
    "resolve_workspace_path",
    "apply_result_budget",
]
