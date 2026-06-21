"""FastAPI 应用定义，提供 HTTP 接口供 Electron 壳调用。

路由：
  GET  /api/state       — 获取会话状态
  POST /api/chat        — SSE 流式对话
  POST /api/command     — 斜杠命令
  POST /api/permission  — 回传权限决策

根路径 "/" 由 StaticFiles 挂载的前端构建产物（frontend/dist）接管。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from query.loop import LoopResult
from query.services.api.llm import StreamEvent
from tools.commands.commands import find_command
from tools.commands.commands_context import CommandContext

# 全局变量，由 __main__.py 启动时设置
app_state: Any = None
engine: Any = None
permission_bridge: Any = None

app = FastAPI(title="Common Code Server")

# 开发阶段允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# GET /api/state — 获取会话状态
# ---------------------------------------------------------------------------


@app.get("/api/state")
async def get_state() -> dict:
    """返回会话状态：消息历史、模型、token 用量、成本。"""
    state = app_state.get_state()
    usage = state.token_usage
    return {
        "messages": engine.mutable_messages,
        "model": state.model,
        "token_usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        },
        "total_cost_usd": state.total_cost_usd,
    }


# ---------------------------------------------------------------------------
# 事件序列化
# ---------------------------------------------------------------------------


def serialize_event(event: Any) -> dict:
    """把引擎事件序列化为 JSON 字典。

    引擎 yield 三种事件：
      - StreamEvent: 流式事件（content/usage/error/done/tool_call_delta）
      - dict: OpenAI 格式消息（assistant/tool/compact boundary）
      - LoopResult: 循环退出结果

    只放非 None 的字段，避免前端收到一堆 null。
    """
    if isinstance(event, StreamEvent):
        result: dict = {"type": "stream", "event_type": event.type}
        if event.content is not None:
            result["content"] = event.content
        if event.usage is not None:
            result["usage"] = event.usage
        if event.error is not None:
            result["error"] = str(event.error)
        if event.finish_reason is not None:
            result["finish_reason"] = event.finish_reason
        return result

    if isinstance(event, dict):
        return {"type": "message", "message": event}

    if isinstance(event, LoopResult):
        result = {"type": "loop_result", "reason": event.reason}
        if event.error is not None:
            result["error"] = str(event.error)
        return result

    # 未知事件类型，兜底处理
    return {"type": "unknown", "data": str(event)}


# ---------------------------------------------------------------------------
# POST /api/chat — SSE 流式对话
# ---------------------------------------------------------------------------


async def chat_event_stream(prompt: str):
    """SSE 事件生成器。

    引擎的 submitMessage 是 async generator，当它挂起在 permission_prompt 回调时
    （await future 等待前端决策），生成器需要能继续推 permission_request 事件。

    实现方式：用后台任务消费引擎事件放入队列，SSE 生成器从队列出队。
    队列空时（引擎可能挂起在权限回调），轮询权限桥推 permission_request 事件。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def consume_engine():
        """后台任务：消费引擎事件入队。"""
        try:
            async for ev in engine.submitMessage(
                prompt, user_context={}, system_context={}
            ):
                await queue.put(ev)
        except Exception as e:
            await queue.put(e)
        finally:
            # 哨兵，表示引擎结束
            await queue.put(None)

    task = asyncio.create_task(consume_engine())

    try:
        while True:
            try:
                # 短超时轮询，让生成器有机会在引擎挂起时推权限请求
                ev = await asyncio.wait_for(queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                # 队列空，检查有没有待推送的权限请求
                req = permission_bridge.get_pending_permission_request()
                if req is not None:
                    yield f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n"
                continue

            if ev is None:
                # 引擎结束，退出循环
                break

            if isinstance(ev, Exception):
                # 引擎抛异常，推错误事件
                yield f"data: {json.dumps({'type': 'error', 'error': str(ev)}, ensure_ascii=False)}\n\n"
                break

            yield f"data: {json.dumps(serialize_event(ev), ensure_ascii=False, default=str)}\n\n"

            # 每个事件后也检查权限请求
            req = permission_bridge.get_pending_permission_request()
            if req is not None:
                yield f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n"
    finally:
        # 客户端断开或引擎结束，清理后台任务
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@app.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    """SSE 流式对话接口。

    请求体：{"prompt": "..."}
    返回：text/event-stream，每行 data: {JSON}\n\n
    """
    prompt = body.get("prompt", "")
    return StreamingResponse(
        chat_event_stream(prompt),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /api/command — 斜杠命令
# ---------------------------------------------------------------------------


@app.post("/api/command")
async def run_command(body: dict) -> dict:
    """斜杠命令接口。

    请求体：{"command": "/cost"}
    返回：{"output": "..."}

    简化处理：命令只读不写消息历史，传消息副本避免修改引擎消息。
    """
    command = body.get("command", "")
    parts = command.strip().split(None, 1)
    cmd_name = parts[0].lower().lstrip("/")
    cmd_args = parts[1] if len(parts) > 1 else ""

    cmd = find_command(cmd_name)
    if cmd is None:
        return {"output": f"Unknown command: /{cmd_name}. Type /help for available commands."}

    # 传消息副本，避免命令修改引擎维护的消息历史
    ctx = CommandContext(
        messages=list(engine.mutable_messages),
        app_state=app_state,
        args=cmd_args,
    )
    output = await cmd.handler(ctx)
    return {"output": output}


# ---------------------------------------------------------------------------
# POST /api/permission — 回传权限决策
# ---------------------------------------------------------------------------


@app.post("/api/permission")
async def resolve_permission(body: dict) -> dict:
    """权限决策回传接口。

    请求体：{"request_id": "...", "decision": "allow"/"deny"/"always_allow"}
    返回：{"ok": true} 或 {"ok": false, "error": "request not found"}
    """
    request_id = body.get("request_id", "")
    decision = body.get("decision", "")
    ok = permission_bridge.resolve(request_id, decision)
    if ok:
        return {"ok": True}
    return {"ok": False, "error": "request not found"}


# ---------------------------------------------------------------------------
# GET /api/files/list — 列目录
# ---------------------------------------------------------------------------

# 这些目录不展示给前端
_EXCLUDED_DIRS = {"__pycache__", "node_modules", "dist", ".git"}


def _project_root() -> str:
    """返回项目根目录（server 的上级目录）。"""
    return os.path.dirname(os.path.dirname(__file__))


def _is_within_root(target: str, root: str) -> bool:
    """判断 target 是否仍在 root 目录内（含 root 本身）。"""
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        # 跨驱动器等情况，直接拒绝
        return False


@app.get("/api/files/list")
async def list_files(path: str = ".") -> dict:
    """列目录接口。

    参数 path：相对路径，默认 "."（项目根目录）。
    返回 {"items": [{"name", "type", "path"}]}，
    目录排前面、文件排后面，各自按名字排序。
    隐藏文件和指定目录会被排除。
    """
    root = _project_root()
    target = os.path.normpath(os.path.join(root, path))

    # 路径安全检查：不允许穿越到项目根之外
    if not _is_within_root(target, root):
        return {"items": []}

    if not os.path.isdir(target):
        return {"items": []}

    dirs: list[dict] = []
    files: list[dict] = []
    for name in os.listdir(target):
        # 排除隐藏文件
        if name.startswith("."):
            continue
        full = os.path.join(target, name)
        rel = os.path.relpath(full, root).replace("\\", "/")
        if os.path.isdir(full):
            if name in _EXCLUDED_DIRS:
                continue
            dirs.append({"name": name, "type": "dir", "path": rel})
        else:
            files.append({"name": name, "type": "file", "path": rel})

    dirs.sort(key=lambda x: x["name"])
    files.sort(key=lambda x: x["name"])
    return {"items": dirs + files}


# ---------------------------------------------------------------------------
# GET /api/files/read — 读文件内容
# ---------------------------------------------------------------------------

# 扩展名到语言标识的映射
_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".txt": "plaintext",
}


@app.get("/api/files/read")
async def read_file(path: str) -> Any:
    """读文件内容接口。

    参数 path：相对路径。
    返回 {"content": "...", "language": "..."}。
    文件不存在返回 404，路径穿越返回 403。
    """
    root = _project_root()
    target = os.path.normpath(os.path.join(root, path))

    # 路径安全检查：不允许 .. 路径穿越
    if not _is_within_root(target, root):
        return JSONResponse(status_code=403, content={"error": "path traversal denied"})

    if not os.path.isfile(target):
        return JSONResponse(status_code=404, content={"error": "file not found"})

    try:
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return JSONResponse(status_code=500, content={"error": "read failed"})

    ext = os.path.splitext(path)[1].lower()
    language = _EXT_TO_LANG.get(ext, "plaintext")
    return {"content": content, "language": language}


# ---------------------------------------------------------------------------
# GET /api/git/status — Git 状态
# ---------------------------------------------------------------------------


def _parse_porcelain_line(line: str) -> dict | None:
    """解析 git status --porcelain 的一行。

    --porcelain 输出格式：XY path，X 是暂存区状态，Y 是工作区状态。
    返回 {"path": "...", "status": "..."}，无法解析时返回 None。
    """
    if len(line) < 4:
        return None
    x = line[0]
    y = line[1]
    # 路径从第 4 个字符开始（XY + 空格）
    file_path = line[3:]

    # 优先看工作区状态 Y，为空再看暂存区状态 X
    code = y if y != " " else x
    status_map = {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "?": "added",  # 未跟踪文件按 added 处理
        "R": "modified",  # 重命名按 modified 处理
        "C": "modified",  # 复制按 modified 处理
    }
    return {"path": file_path, "status": status_map.get(code, "unknown")}


@app.get("/api/git/status")
async def git_status() -> dict:
    """Git 状态接口。

    返回 {"branch": "...", "changes": [{"path", "status"}]}。
    不在 git 仓库或调用失败时返回空分支和空变更列表。
    """
    root = _project_root()

    try:
        branch_proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""

        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )

        changes: list[dict] = []
        if status_proc.returncode == 0:
            for line in status_proc.stdout.splitlines():
                parsed = _parse_porcelain_line(line)
                if parsed is not None:
                    changes.append(parsed)

        return {"branch": branch, "changes": changes}
    except (subprocess.SubprocessError, OSError):
        return {"branch": "", "changes": []}


# ---------------------------------------------------------------------------
# POST /api/abort — 取消当前查询（占位）
# ---------------------------------------------------------------------------


@app.post("/api/abort")
async def abort_query() -> JSONResponse:
    """取消当前查询（占位实现）。

    返回 501 状态码和 {"error": "abort not implemented"}。
    """
    return JSONResponse(status_code=501, content={"error": "abort not implemented"})


# ---------------------------------------------------------------------------
# 静态文件 — 挂载前端构建产物
# ---------------------------------------------------------------------------
# 必须放在所有 API 路由（/api/*）之后，否则 /api/* 请求会被静态文件拦截。
# server/__main__.py 运行时 cwd 是项目根目录，前端构建产物在 frontend/dist。
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
