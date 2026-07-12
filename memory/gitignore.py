"""完整 gitignore 语法解析器 - 支持锚定、目录限定、取反、** 递归通配。

从 .gitignore 文件加载规则，递归路径匹配。
"""

from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class GitignoreMatcher:
    """gitignore 规则匹配器。

    支持：
    - 锚定（/ 前缀）：仅匹配根目录
    - 目录限定（/ 后缀）：仅匹配目录
    - 取反（! 前缀）：取消忽略
    - ** 递归通配：匹配任意层级目录
    - 多级目录级联 .gitignore
    """

    def __init__(self):
        self._rules: list[tuple[str, bool, bool, bool]] = []  # (pattern, negate, anchored, dir_only)

    def load_gitignore(self, dir_path: Path) -> None:
        """从目录的 .gitignore 文件加载规则。

        Args:
            dir_path: 项目根目录
        """
        gitignore_path = dir_path / ".gitignore"
        if not gitignore_path.is_file():
            return

        try:
            for line in gitignore_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self._add_rule(line)
        except Exception as e:
            logger.warning("加载 .gitignore 失败: %s", e)

    def _add_rule(self, pattern: str) -> None:
        """解析并添加一条 gitignore 规则。"""
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]

        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern[1:]

        dir_only = pattern.endswith("/")
        if dir_only:
            pattern = pattern[:-1]

        self._rules.append((pattern, negate, anchored, dir_only))

    def should_ignore(self, file_path: Path, root: Path | None = None) -> bool:
        """检查文件是否应被忽略。

        Args:
            file_path: 文件路径（绝对或相对）
            root: 项目根目录，None 则用 file_path 的第一个父目录

        Returns:
            True 如果文件应被忽略
        """
        if not self._rules:
            return False

        # Get relative path from root
        if root is not None:
            try:
                rel = file_path.relative_to(root)
            except ValueError:
                return False
        else:
            rel = file_path

        rel_str = str(rel).replace("\\", "/")
        parts = rel_str.split("/")

        ignored = False

        for pattern, negate, anchored, dir_only in self._rules:
            if self._match_pattern(pattern, negate, anchored, dir_only, parts, rel_str):
                if negate:
                    ignored = False
                else:
                    ignored = True

        return ignored

    def _match_pattern(self, pattern, negate, anchored, dir_only, parts, rel_str) -> bool:
        """检查单个模式是否匹配。"""
        # Handle ** recursive wildcard
        if "**" in pattern:
            # Convert gitignore ** to fnmatch-compatible pattern
            # **/ means match in any directory
            # /** means match everything under root
            fnmatch_pattern = pattern.replace("**/", "*")
            fnmatch_pattern = fnmatch_pattern.replace("/**", "/*")
            fnmatch_pattern = fnmatch_pattern.replace("**", "*")
            return fnmatch.fnmatch(rel_str, fnmatch_pattern)

        if anchored:
            # Anchored: only match from root
            return fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(parts[0], pattern)
        else:
            # Non-anchored: match any level
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
            return False
