"""FastAPI 服务入口，供 Electron 壳调用。

启动流程：
1. setup() 初始化基础设施和会话状态（环境变量、配置、向量模型、hooks、AppState）
2. 加载插件（LLM 供应商、记忆后端），重捕 hooks 快照纳入插件提供的 hooks
3. 构造权限桥和引擎，装配全局变量和会话存储供路由访问
4. 启动 uvicorn，往 stdout 写端口 JSON 供 Electron 读取

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
from server import state as server_state
from server.permission_bridge import PermissionBridge
from server.question_bridge import QuestionBridge
from startup.setup import setup


def find_free_port() -> int:
    """让系统分配一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    """主入口：初始化 -> 加载插件 -> 构造引擎 -> 启动服务。"""
    # 1. 基础设施初始化 + 会话状态搭建
    app_state = await setup()

    # 2. 加载插件：扫描启用、注册 LLM 供应商和记忆后端
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

    # 重捕 hooks 快照，纳入插件提供的 hooks
    from startup.setup import update_hooks_snapshot

    update_hooks_snapshot()

    # 3. 构造权限桥、提问桥和引擎
    bridge = PermissionBridge()
    q_bridge = QuestionBridge()
    config = build_engine_config(
        permission_prompt=bridge.request_permission,
        question_prompt=q_bridge.ask_question,
    )
    engine = QueryEngine(config)

    # 装配全局变量和会话存储，供路由访问
    server_state.app_state = app_state
    server_state.engine = engine
    server_state.permission_bridge = bridge
    server_state.question_bridge = q_bridge

    from session.store import SessionStore

    server_state.session_store = SessionStore()

    # 4. 启动服务，写端口 JSON 给 Electron
    port = find_free_port()
    uvicorn_config = uvicorn.Config(
        app=app_module.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)

    # 在后台启动，等它开始监听后打印端口
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)

    # 服务启动后触发 SessionStart hooks，收集额外上下文供后续使用
    from startup.bootstrap.state import get_cwd_state
    from startup.hooks import run_session_start_hooks
    from startup.setup import get_hooks_snapshot

    hook_snapshot = get_hooks_snapshot()
    session_start_context = ""
    if hook_snapshot is not None:
        try:
            session_start_context = await run_session_start_hooks(
                hook_snapshot,
                "",
                get_cwd_state(),
            )
        except Exception as e:
            print(f"SessionStart hooks 执行失败: {e}", file=sys.stderr)
    server_state.session_start_context = session_start_context

    sys.stdout.write(json.dumps({"port": port}) + "\n")
    sys.stdout.flush()

    # 等待服务结束
    await task

    # 服务退出时触发 SessionEnd hooks，做清理
    from startup.hooks import run_session_end_hooks

    if hook_snapshot is not None:
        try:
            await run_session_end_hooks(
                hook_snapshot,
                "",
                get_cwd_state(),
            )
        except Exception as e:
            print(f"SessionEnd hooks 执行失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
