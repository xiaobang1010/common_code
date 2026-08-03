"""bash_tool — 依赖注册。"""

from tools.implementations.bash_tool.schema import BashInput
from tools.implementations.bash_tool.tool import get_bash_tool

__all__ = ["get_bash_tool", "BashInput"]
