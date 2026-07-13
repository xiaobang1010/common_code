"""FastAPI 服务入口，供 Electron 壳调用。

启动流程：
1. init() + setup() 初始化基础设施和会话状态
2. 构造 PermissionBridge 和 QueryEngine
3. 用 uvicorn 启动 HTTP 服务
4. 往 stdout 写端口 JSON，让 Electron 主进程读到端口

用法：uv run python -m server
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys

import uvicorn

from query.engine import QueryEngine, build_engine_config
from server import app as app_module
from server.permission_bridge import PermissionBridge
from startup.entrypoints.init import init
from startup.setup import setup


def find_free_port() -> int:
    """让系统分配一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    """主入口：初始化 → 构造引擎 → 启动服务 → 打印端口。"""
    # 1. 基础设施初始化
    init()

    # 1.5 插件系统初始化 — 扫描插件、加载 LLM 供应商和记忆后端
    # 这一步让设置面板能看到已安装的插件、可切换的供应商和记忆后端
    from startup.plugins import init_plugins
    init_plugins()
    try:
        from query.services.api.providers import load_llm_provider_plugins
        load_llm_provider_plugins()
    except Exception as e:
        print(f"加载 LLM 供应商插件失败: {e}", file=sys.stderr)
    try:
        from query.services.memory.registry import load_memory_plugins
        load_memory_plugins()
    except Exception as e:
        print(f"加载记忆插件失败: {e}", file=sys.stderr)

    # 2. 会话状态搭建
    app_state = await setup()

    # 3. 构造权限桥和引擎
    bridge = PermissionBridge()
    config = build_engine_config(permission_prompt=bridge.request_permission)
    engine = QueryEngine(config)

    # 4. 设置 app 全局变量，让路由能访问
    app_module.app_state = app_state
    app_module.engine = engine
    app_module.permission_bridge = bridge

    # 初始化会话存储层
    from session.store import SessionStore
    app_module.session_store = SessionStore()

    # 5. 分配端口
    port = find_free_port()

    # 6. 启动 uvicorn
    uvicorn_config = uvicorn.Config(
        app=app_module.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)

    # 在后台启动服务，等它开始监听后打印端口
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)

    # 往 stdout 写端口 JSON，让 Electron 主进程读到
    sys.stdout.write(json.dumps({"port": port}) + "\n")
    sys.stdout.flush()

    # 等待服务结束
    await task


if __name__ == "__main__":
    asyncio.run(main())
