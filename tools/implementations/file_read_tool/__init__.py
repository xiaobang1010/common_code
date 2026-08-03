"""file_read_tool — 依赖注册。"""

from tools.implementations.file_read_tool.schema import FileReadInput
from tools.implementations.file_read_tool.tool import get_file_read_tool

__all__ = ["get_file_read_tool", "FileReadInput"]
