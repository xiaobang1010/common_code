"""show_widget 工具包：只导出装配入口与输入模型。"""

from tools.implementations.show_widget_tool.schema import ShowWidgetInput
from tools.implementations.show_widget_tool.tool import get_show_widget_tool

__all__ = ["get_show_widget_tool", "ShowWidgetInput"]
