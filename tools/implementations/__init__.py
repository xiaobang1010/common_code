"""内置工具实现 — 依赖注册。"""

from tools.implementations.bash_tool import BashInput, get_bash_tool
from tools.implementations.file_edit_tool import FileEditInput, get_file_edit_tool
from tools.implementations.file_read_tool import FileReadInput, get_file_read_tool
from tools.implementations.file_write_tool import FileWriteInput, get_file_write_tool
from tools.implementations.glob_tool import GlobInput, get_glob_tool
from tools.implementations.grep_tool import GrepInput, get_grep_tool
from tools.implementations.present_files_tool import (
    PresentFilesInput,
    get_present_files_tool,
)
from tools.implementations.show_widget_tool import ShowWidgetInput, get_show_widget_tool
from tools.implementations.todo_write_tool import TodoWriteInput, get_todo_write_tool
from tools.implementations.web_fetch_tool import WebFetchInput, get_web_fetch_tool
from tools.implementations.widget_guidelines_tool import (
    WidgetGuidelinesInput,
    get_widget_guidelines_tool,
)

__all__ = [
    "get_bash_tool",
    "BashInput",
    "get_file_read_tool",
    "FileReadInput",
    "get_file_edit_tool",
    "FileEditInput",
    "get_file_write_tool",
    "FileWriteInput",
    "get_glob_tool",
    "GlobInput",
    "get_grep_tool",
    "GrepInput",
    "get_present_files_tool",
    "PresentFilesInput",
    "get_show_widget_tool",
    "ShowWidgetInput",
    "get_todo_write_tool",
    "TodoWriteInput",
    "get_web_fetch_tool",
    "WebFetchInput",
    "get_widget_guidelines_tool",
    "WidgetGuidelinesInput",
]
