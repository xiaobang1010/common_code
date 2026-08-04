"""FastAPI 应用定义，提供 HTTP 接口供 Electron 壳调用。

路由按业务域拆分到 server/routers/ 下的子包中，
本文件只负责创建 FastAPI 实例、挂载 CORS、include 路由器、挂载静态文件。

根路径 "/" 由 StaticFiles 挂载的前端构建产物（frontend/dist）接管。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.routers import (
    chat_router,
    skills_router,
    permission_router,
    files_router,
    git_router,
    search_router,
    config_router,
    plugins_router,
    memory_router,
    agents_router,
    sessions_router,
    workspaces_router,
)

app = FastAPI(title="Common Code Server")

# 开发阶段允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载各业务域路由
app.include_router(chat_router)
app.include_router(skills_router)
app.include_router(permission_router)
app.include_router(files_router)
app.include_router(git_router)
app.include_router(search_router)
app.include_router(config_router)
app.include_router(plugins_router)
app.include_router(memory_router)
app.include_router(agents_router)
app.include_router(sessions_router)
app.include_router(workspaces_router)

# 静态文件 - 挂载前端构建产物
# 必须放在所有 API 路由（/api/*）之后，否则 /api/* 请求会被静态文件拦截。
# server/__main__.py 运行时 cwd 是项目根目录，前端构建产物在 frontend/dist。
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
