"""Server 全局状态，由 __main__.py 启动时设置，路由模块读取。"""

from __future__ import annotations

from typing import Any

# 由 __main__.py 启动时设置
app_state: Any = None
engine: Any = None
permission_bridge: Any = None
session_store: Any = None
# 当前对话任务，用于 abort 接口取消
current_task: Any = None
