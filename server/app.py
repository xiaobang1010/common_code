"""FastAPI 应用定义，提供 HTTP 接口供 Electron 壳调用。

路由：
  GET  /api/state       — 获取会话状态
  POST /api/chat        — SSE 流式对话
  POST /api/command     — 斜杠命令（含 skill 触发）
  GET  /api/skills      — 获取可用 skill 列表
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
from query.services.api.client import reset_client
from query.services.api.llm import StreamEvent
from query.services.pricing import calculate_cost
from startup.bootstrap.state import add_to_total_cost
from startup.utils.config import get_global_config, save_global_config
from tools.commands.commands import find_command, try_resolve_skill
from tools.commands.commands_context import CommandContext

# 全局变量，由 __main__.py 启动时设置
app_state: Any = None
engine: Any = None
permission_bridge: Any = None
# 当前对话任务，用于 abort 接口取消
current_task: Any = None

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
            # 当前上下文大小（最近一次请求的 prompt_tokens，覆盖不累加）
            "last_prompt_tokens": usage.last_prompt_tokens,
            # 已缓存大小（最近一次请求的 cache_creation_input_tokens，覆盖不累加）
            "last_cache_creation": usage.last_cache_creation,
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
        # 工具调用增量字段，让前端能实时展示"正在调用工具 X"
        if event.tool_call_id is not None:
            result["tool_call_id"] = event.tool_call_id
        if event.tool_call_name is not None:
            result["tool_call_name"] = event.tool_call_name
        if event.tool_call_arguments is not None:
            result["tool_call_arguments"] = event.tool_call_arguments
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
        """后台任务：消费引擎事件入队，同时累加 token 和成本到 AppState。"""
        try:
            async for ev in engine.submitMessage(
                prompt, user_context={}, system_context={}
            ):
                # 拦截 usage 事件，累加 token 和成本到 AppState（和 repl.py 逻辑一致）
                if isinstance(ev, StreamEvent) and ev.type == "usage" and ev.usage:
                    state = app_state.get_state()
                    prompt_tokens = ev.usage.get("prompt_tokens", 0)
                    completion_tokens = ev.usage.get("completion_tokens", 0)
                    cache_read = ev.usage.get("cache_read_input_tokens", 0)
                    cache_creation = ev.usage.get("cache_creation_input_tokens", 0)
                    state.token_usage.input_tokens += prompt_tokens
                    state.token_usage.output_tokens += completion_tokens
                    state.token_usage.cache_read_input_tokens += cache_read
                    state.token_usage.cache_creation_input_tokens += cache_creation
                    # 最近一次请求的上下文大小和缓存大小（覆盖，不累加）
                    state.token_usage.last_prompt_tokens = prompt_tokens
                    state.token_usage.last_cache_creation = cache_creation
                    cost = calculate_cost(state.model or "", ev.usage)
                    state.total_cost_usd += cost
                    add_to_total_cost(
                        cost,
                        {"input_tokens": prompt_tokens, "output_tokens": completion_tokens},
                        state.model or "",
                    )
                await queue.put(ev)
        except Exception as e:
            await queue.put(e)
        finally:
            # 哨兵，表示引擎结束
            await queue.put(None)

    task = asyncio.create_task(consume_engine())
    # 记录到全局变量，供 /api/abort 取消
    global current_task
    current_task = task

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
                else:
                    # 推心跳，让前端知道后端还活着（AI 可能在思考或执行工具）
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
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
        # 清理全局引用
        current_task = None


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
    返回：
      普通命令 → {"output": "..."}
      skill 触发 → {"is_skill": true, "skill_prompt": "...", "skill_name": "..."}
      未知命令 → {"output": "Unknown command: ..."}
    """
    command = body.get("command", "")
    parts = command.strip().split(None, 1)
    cmd_name = parts[0].lower().lstrip("/")
    cmd_args = parts[1] if len(parts) > 1 else ""

    # 先查内置命令
    cmd = find_command(cmd_name)
    if cmd is not None:
        ctx = CommandContext(
            messages=list(engine.mutable_messages),
            app_state=app_state,
            args=cmd_args,
        )
        output = await cmd.handler(ctx)
        return {"output": output}

    # 内置命令没命中 → 尝试 skill 触发
    skill_msg = try_resolve_skill(cmd_name, cmd_args)
    if skill_msg is not None:
        return {
            "is_skill": True,
            "skill_prompt": skill_msg["content"],
            "skill_name": cmd_name,
        }

    return {"output": f"Unknown command: /{cmd_name}. Type /help for available commands."}


# ---------------------------------------------------------------------------
# GET /api/skills — 获取可用 skill 列表（供前端命令补全）
# ---------------------------------------------------------------------------


@app.get("/api/skills")
async def list_skills() -> dict:
    """返回所有用户可调用的 skill 列表（含来源与完整元数据）。

    返回：{"skills": [{"name", "description", "when_to_use", "source",
                        "source_label", "allowed_tools", "disable_model_invocation",
                        "user_invocable", "skill_root"}, ...]}
    """
    from tools.skills.bundled import get_all_skills
    from tools.skills.loader import classify_skill_source

    skills = get_all_skills()
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "when_to_use": s.when_to_use or "",
                "source": s.source,
                "source_label": classify_skill_source(s),
                "allowed_tools": s.allowed_tools,
                "disable_model_invocation": s.disable_model_invocation,
                "user_invocable": s.user_invocable,
                "skill_root": s.skill_root,
            }
            for s in skills
            if s.is_user_invocable()
        ]
    }


# ---------------------------------------------------------------------------
# POST /api/permission — 回传权限决策
# ---------------------------------------------------------------------------


@app.post("/api/skills/create")
async def create_skill(body: dict) -> dict:
    """新建技能。请求体：{"name", "description", "when_to_use", "allowed_tools"}"""
    from tools.skills.loader import create_skill_file

    name = body.get("name", "").strip()
    description = body.get("description", "").strip()
    when_to_use = body.get("when_to_use", "").strip()
    allowed_tools = body.get("allowed_tools") or None

    if not name or not description:
        return {"ok": False, "error": "name 和 description 必填"}

    try:
        create_skill_file(name, description, when_to_use, allowed_tools)
        return {"ok": True, "name": name}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/skills/import")
async def import_skill(body: dict) -> dict:
    """导入技能。请求体：{"name", "content"}"""
    from tools.skills.loader import import_skill_file

    name = body.get("name", "").strip()
    content = body.get("content", "")

    if not name or not content:
        return {"ok": False, "error": "name 和 content 必填"}

    try:
        import_skill_file(name, content)
        return {"ok": True, "name": name}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/skills/refresh")
async def refresh_skills() -> dict:
    """刷新技能缓存，重新扫描文件系统。"""
    from tools.skills.loader import clear_cache
    clear_cache()
    return {"ok": True}


@app.post("/api/skills/delete")
async def delete_skill(body: dict) -> dict:
    """删除用户级技能。请求体：{"name"}"""
    from tools.skills.loader import delete_skill_file

    name = body.get("name", "").strip()
    if not name:
        return {"ok": False, "error": "name 必填"}

    try:
        delete_skill_file(name)
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


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


def _parse_porcelain_line(line: str) -> list[dict]:
    """解析 git status --porcelain 的一行，返回变更项列表。

    --porcelain 输出格式：XY path，X 是暂存区状态，Y 是工作区状态。
    一个文件可能同时有暂存和未暂存的改动，此时返回两项。
    返回 [{"path": "...", "status": "...", "staged": True/False}, ...]，无法解析时返回空列表。
    """
    if len(line) < 4:
        return []
    x = line[0]
    y = line[1]
    # 路径从第 4 个字符开始（XY + 空格）
    file_path = line[3:]

    status_map = {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "modified",  # 重命名按 modified 处理
        "C": "modified",  # 复制按 modified 处理
        "?": "added",  # 未跟踪文件按 added 处理
    }

    changes: list[dict] = []
    # 暂存区状态 X：空格或问号表示无暂存改动
    if x not in (" ", "?"):
        changes.append(
            {"path": file_path, "status": status_map.get(x, "unknown"), "staged": True}
        )
    # 工作区状态 Y：空格表示无未暂存改动
    if y != " ":
        changes.append(
            {"path": file_path, "status": status_map.get(y, "unknown"), "staged": False}
        )
    return changes


@app.get("/api/git/status")
async def git_status() -> dict:
    """Git 状态接口。

    返回 {"branch": "...", "changes": [{"path", "status", "staged"}]}。
    其中 staged 为 True 表示已暂存，False 表示未暂存。
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
                if parsed:
                    changes.extend(parsed)

        return {"branch": branch, "changes": changes}
    except (subprocess.SubprocessError, OSError):
        return {"branch": "", "changes": []}


# ---------------------------------------------------------------------------
# POST /api/abort — 取消当前查询（占位）
# ---------------------------------------------------------------------------


@app.post("/api/abort")
async def abort_query() -> JSONResponse:
    """取消当前正在进行的对话任务。

    通过取消 consume_engine 后台任务来中断对话，
    SSE 流会因任务取消而结束，前端收到连接关闭后恢复输入状态。
    """
    global current_task
    if current_task is not None and not current_task.done():
        current_task.cancel()
        try:
            await current_task
        except asyncio.CancelledError:
            pass
        current_task = None
        return JSONResponse(content={"ok": True})
    return JSONResponse(content={"ok": False, "error": "no running task"})


# ---------------------------------------------------------------------------
# GET /api/search — 全局搜索
# ---------------------------------------------------------------------------


@app.get("/api/search")
async def search(
    q: str,
    case_sensitive: bool = False,
    regex: bool = False,
) -> dict:
    """全局搜索接口，用 ripgrep 搜索项目文件内容。

    参数 q：搜索关键词。
    参数 case_sensitive：是否区分大小写，默认 false。
    参数 regex：是否按正则匹配，默认 false。
    返回 {"results": [{"path", "line_number", "line", "matches"}]}。
    rg 不存在或调用失败时返回空结果和 error 字段。
    """
    root = _project_root()

    # 构建 rg 命令：JSON 输出、带行号、每文件最多 50 个匹配
    cmd: list[str] = ["rg", "--json", "-n", "--max-count", "50"]

    # 默认不区分大小写；区分大小写时不加 -i
    if not case_sensitive:
        cmd.append("-i")

    # 非正则模式按字面字符串匹配
    if not regex:
        cmd.append("--fixed-strings")

    # 排除常见无关目录
    cmd.extend(
        [
            "-g", "!node_modules",
            "-g", "!__pycache__",
            "-g", "!.git",
            "-g", "!dist",
        ]
    )

    cmd.append(q)
    cmd.append(".")

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return {"results": [], "error": "ripgrep not found"}

    # rg --json 每行输出一个 JSON 对象，只取 type=="match" 的
    results: list[dict] = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data", {})
        path = data.get("path", {}).get("text", "")
        line_number = data.get("line_number", 0)
        line_text = data.get("lines", {}).get("text", "")
        submatches = data.get("submatches", [])
        matches = [
            {"start": sm.get("start", 0), "end": sm.get("end", 0)}
            for sm in submatches
        ]
        results.append(
            {
                "path": path,
                "line_number": line_number,
                "line": line_text,
                "matches": matches,
            }
        )

    return {"results": results}


# ---------------------------------------------------------------------------
# POST /api/git/stage — 暂存文件
# ---------------------------------------------------------------------------


@app.post("/api/git/stage")
async def git_stage(body: dict) -> dict:
    """暂存文件接口，执行 git add。

    请求体：{"path": "..."}
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    root = _project_root()
    try:
        proc = subprocess.run(
            ["git", "add", path],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/git/unstage — 取消暂存
# ---------------------------------------------------------------------------


@app.post("/api/git/unstage")
async def git_unstage(body: dict) -> dict:
    """取消暂存接口，执行 git reset HEAD。

    请求体：{"path": "..."}
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    root = _project_root()
    try:
        proc = subprocess.run(
            ["git", "reset", "HEAD", path],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/git/commit — 提交
# ---------------------------------------------------------------------------


@app.post("/api/git/commit")
async def git_commit(body: dict) -> dict:
    """提交接口，执行 git commit -m。

    请求体：{"message": "..."}
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    message = body.get("message", "")
    if not message:
        return {"ok": False, "error": "message is required"}
    root = _project_root()
    try:
        proc = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /api/git/diff — 获取文件 diff
# ---------------------------------------------------------------------------


@app.get("/api/git/diff")
async def git_diff(path: str = "") -> dict:
    """获取文件 diff 接口，执行 git diff。

    参数 path：文件路径，可选。
    返回 {"diff": "..."}。
    """
    root = _project_root()
    cmd = ["git", "diff"]
    if path:
        cmd.append(path)
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return {"diff": ""}
    return {"diff": proc.stdout}


# ---------------------------------------------------------------------------
# GET /api/config — 读取 LLM 配置
# ---------------------------------------------------------------------------


@app.get("/api/config")
async def get_config() -> dict:
    """读取 LLM 配置接口。

    返回 {"llm_base_url", "llm_api_key", "llm_model", "llm_providers", "active_provider"}。
    配置系统未初始化等异常情况下返回空值和 error 字段。
    """
    try:
        config = get_global_config()

        # 获取 LLM 供应商信息
        llm_providers = []
        active_provider = None
        try:
            from query.services.api.providers import get_registry
            registry = get_registry()
            llm_providers = registry.list_providers()
            active_provider = registry.get_active_name()
        except ImportError:
            pass

        return {
            "llm_base_url": config.llm_base_url or "",
            "llm_api_key": config.llm_api_key or "",
            "llm_model": config.llm_model or "",
            "llm_providers": llm_providers,
            "active_provider": active_provider,
        }
    except Exception as e:
        return {
            "llm_base_url": "",
            "llm_api_key": "",
            "llm_model": "",
            "llm_providers": [],
            "active_provider": None,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# POST /api/config — 写入 LLM 配置
# ---------------------------------------------------------------------------


@app.post("/api/config")
async def set_config(body: dict) -> dict:
    """写入 LLM 配置接口。

    请求体：{"llm_base_url", "llm_api_key", "llm_model"}，只更新传入的字段。
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    try:
        config = get_global_config()
        # 只更新传入的字段，不覆盖未传的字段
        if "llm_base_url" in body:
            config.llm_base_url = body["llm_base_url"]
        if "llm_api_key" in body:
            config.llm_api_key = body["llm_api_key"]
        if "llm_model" in body:
            config.llm_model = body["llm_model"]
        save_global_config(config)

        # 配置变更后：重置 LLM 客户端缓存 + 更新 AppState 的 model 字段
        # 否则引擎还会用旧模型名调 API，报 Invalid model id
        reset_client()
        state = app_state.get_state()
        from query.services.api.client import get_default_model
        state.model = get_default_model()

        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/plugins — 获取已安装插件列表
# ---------------------------------------------------------------------------


@app.get("/api/plugins")
async def list_plugins() -> dict:
    """返回已安装插件列表。

    返回：{"plugins": [{"name", "version", "kind", "enabled", "description",
                        "source", "skills_count", "hooks_count",
                        "commands_count", "mcp_servers_count"}]}
    """
    from startup.plugins.manager import PluginManager

    plugins = PluginManager.get_all_plugins()
    return {
        "plugins": [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "kind": p.manifest.kind,
                "enabled": p.enabled,
                "description": p.manifest.description,
                "source": p.manifest.source,
                "skills_count": len(p.skills_registered),
                "hooks_count": len(p.hooks_registered),
                "commands_count": len(p.commands_registered),
                "mcp_servers_count": len(p.mcp_servers_registered),
            }
            for p in plugins
        ]
    }


# ---------------------------------------------------------------------------
# POST /api/plugins/enable — 启用插件
# ---------------------------------------------------------------------------


@app.post("/api/plugins/enable")
async def enable_plugin(body: dict) -> dict:
    """启用插件。请求体：{"name": "..."}"""
    from startup.plugins.manager import PluginManager

    name = body.get("name", "")
    ok = PluginManager.enable_plugin(name)
    if not ok:
        return {"ok": False, "error": f"Plugin not found: {name}"}
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/plugins/disable — 禁用插件
# ---------------------------------------------------------------------------


@app.post("/api/plugins/disable")
async def disable_plugin(body: dict) -> dict:
    """禁用插件。请求体：{"name": "..."}"""
    from startup.plugins.manager import PluginManager

    name = body.get("name", "")
    ok = PluginManager.disable_plugin(name)
    if not ok:
        return {"ok": False, "error": f"Plugin not found: {name}"}
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/plugins/llm-provider/switch — 切换 LLM 供应商
# ---------------------------------------------------------------------------


@app.post("/api/plugins/llm-provider/switch")
async def switch_llm_provider(body: dict) -> dict:
    """切换 LLM 供应商。请求体：{"provider": "..."}"""
    from query.services.api.providers import get_registry

    provider_name = body.get("provider", "")
    registry = get_registry()
    ok = registry.set_active(provider_name)
    if not ok:
        return {"ok": False, "error": f"LLM provider not found: {provider_name}"}

    # 更新 AppState model
    provider = registry.get_active_provider()
    if provider and app_state:
        state = app_state.get_state()
        state.model = provider.model

    return {"ok": True, "active_provider": provider_name}


# ---------------------------------------------------------------------------
# GET /api/plugins/llm-providers — 获取可用 LLM 供应商列表
# ---------------------------------------------------------------------------


@app.get("/api/plugins/llm-providers")
async def list_llm_providers() -> dict:
    """返回可用 LLM 供应商列表和当前激活的供应商。"""
    from query.services.api.providers import get_registry

    registry = get_registry()
    return {
        "providers": registry.list_providers(),
        "active": registry.get_active_name(),
    }


# ---------------------------------------------------------------------------
# GET /api/memory/providers — 列出记忆后端 + 当前激活
# ---------------------------------------------------------------------------


@app.get("/api/memory/providers")
async def list_memory_providers() -> dict:
    """返回已注册的记忆后端列表和当前激活的后端名。"""
    from query.services.memory.registry import get_registry

    registry = get_registry()
    return {
        "providers": [{"name": name} for name in registry.list_providers()],
        "active": registry.get_active_name(),
    }


# ---------------------------------------------------------------------------
# POST /api/memory/switch — 切换激活记忆后端（持久化）
# ---------------------------------------------------------------------------


@app.post("/api/memory/switch")
async def switch_memory_provider(body: dict) -> dict:
    """切换激活记忆后端。请求体：{"name": "..."}"""
    from query.services.memory.registry import get_registry

    name = body.get("name", "")
    registry = get_registry()
    ok = registry.set_active(name)
    if not ok:
        return {"ok": False, "error": f"Memory provider not found: {name}"}
    return {"ok": True, "active": name}


# ---------------------------------------------------------------------------
# POST /api/memory/clear — 清空指定会话记忆
# ---------------------------------------------------------------------------


@app.post("/api/memory/clear")
async def clear_memory(body: dict) -> dict:
    """清空指定会话的记忆。请求体：{"session_id": "..."}"""
    from query.services.memory.registry import get_registry

    session_id = body.get("session_id", "default")
    registry = get_registry()
    provider = registry.get_active()
    if provider is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    try:
        await provider.clear(session_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"清空记忆失败: {e}"}


# ---------------------------------------------------------------------------
# GET /api/agents — 列出内置子智能体（只读）
# ---------------------------------------------------------------------------


@app.get("/api/agents")
async def list_agents() -> dict:
    """返回内置子智能体定义的只读字段。"""
    from tools.subagent.built_in_agents import get_built_in_agents

    agents = []
    for a in get_built_in_agents():
        agents.append({
            "agent_type": a.agent_type,
            "when_to_use": a.when_to_use,
            "tools": a.tools,
            "disallowed_tools": a.disallowed_tools,
            "model": a.model,
            "max_turns": a.max_turns,
            "background": a.background,
            "source": a.source,
        })
    return {"agents": agents}


# ---------------------------------------------------------------------------
# 静态文件 — 挂载前端构建产物
# ---------------------------------------------------------------------------
# 必须放在所有 API 路由（/api/*）之后，否则 /api/* 请求会被静态文件拦截。
# server/__main__.py 运行时 cwd 是项目根目录，前端构建产物在 frontend/dist。
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
