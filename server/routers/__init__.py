"""路由子包入口，re-export 各业务域的 router。"""

from server.routers.chat import router as chat_router
from server.routers.skills import router as skills_router
from server.routers.permission import router as permission_router
from server.routers.files import router as files_router
from server.routers.git import router as git_router
from server.routers.search import router as search_router
from server.routers.config import router as config_router
from server.routers.plugins import router as plugins_router
from server.routers.memory import router as memory_router
from server.routers.agents import router as agents_router
from server.routers.subagents import router as subagents_router
from server.routers.sessions import router as sessions_router
from server.routers.workspaces import router as workspaces_router

__all__ = [
    "chat_router",
    "skills_router",
    "permission_router",
    "files_router",
    "git_router",
    "search_router",
    "config_router",
    "plugins_router",
    "memory_router",
    "agents_router",
    "subagents_router",
    "sessions_router",
    "workspaces_router",
]
