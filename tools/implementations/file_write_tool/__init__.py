"""file_write_tool — 依赖注册。"""

from tools.implementations.file_write_tool.schema import FileWriteInput
from tools.implementations.file_write_tool.tool import get_file_write_tool

__all__ = ["get_file_write_tool", "FileWriteInput"]
