"""file_edit_tool — 依赖注册。"""

from tools.implementations.file_edit_tool.schema import FileEditInput
from tools.implementations.file_edit_tool.tool import get_file_edit_tool

__all__ = ["get_file_edit_tool", "FileEditInput"]
