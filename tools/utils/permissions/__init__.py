"""权限模块。

导出权限决策系统的所有公开类和函数。
"""

from tools.utils.permissions.auto_classifier import AutoClassifier
from tools.utils.permissions.permissions import (
    PermissionDecision,
    PermissionResult,
    check_rule_based_permissions,
    has_permissions_to_use_tool,
    is_safe_path,
    matches_input_pattern,
    matches_tool_pattern,
)

__all__ = [
    "AutoClassifier",
    "PermissionDecision",
    "PermissionResult",
    "check_rule_based_permissions",
    "has_permissions_to_use_tool",
    "is_safe_path",
    "matches_input_pattern",
    "matches_tool_pattern",
]
