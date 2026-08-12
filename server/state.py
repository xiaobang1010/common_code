"""Server 全局状态，由 __main__.py 启动时设置，路由模块读取。"""

from __future__ import annotations

from typing import Any

# 由 __main__.py 启动时设置
app_state: Any = None
engine: Any = None
permission_bridge: Any = None
# AskUserQuestion 提问桥，把模型提问转成 SSE 事件推给前端
question_bridge: Any = None
session_store: Any = None
# 当前对话任务，用于 abort 接口取消
current_task: Any = None
# 当前运行任务所属的会话 id（与 current_task 同步设置/清空，
# 供列表 API 透出"哪个任务在运行"，不落库）
current_session_id: Any = None
