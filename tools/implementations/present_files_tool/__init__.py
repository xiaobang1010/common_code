"""present_files_tool — 依赖注册。"""

from tools.implementations.present_files_tool.schema import PresentFilesInput
from tools.implementations.present_files_tool.tool import get_present_files_tool

__all__ = ["get_present_files_tool", "PresentFilesInput"]
