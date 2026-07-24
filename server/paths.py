"""工作区路径管理与路径安全检查。"""

from __future__ import annotations

import os


# 这些目录不展示给前端
EXCLUDED_DIRS = {"__pycache__", "node_modules", "dist", ".git"}

# 扩展名到语言标识的映射
EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".txt": "plaintext",
}

# 可变的项目根目录，支持工作区切换
_project_root_value: str = os.path.dirname(os.path.dirname(__file__))


def project_root() -> str:
    """返回当前工作区根目录。"""
    return _project_root_value


def set_project_root(path: str) -> None:
    """切换工作区根目录。"""
    global _project_root_value
    _project_root_value = path


def is_within_root(target: str, root: str) -> bool:
    """判断 target 是否仍在 root 目录内（含 root 本身）。"""
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        # 跨驱动器等情况，直接拒绝
        return False
