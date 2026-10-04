"""web_fetch_tool — 依赖注册。"""

from tools.implementations.web_fetch_tool.schema import WebFetchInput
from tools.implementations.web_fetch_tool.tool import get_web_fetch_tool

__all__ = ["get_web_fetch_tool", "WebFetchInput"]
