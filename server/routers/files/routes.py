"""文件相关路由：列目录、读文件、写文件、新建文件/目录、文件变更事件。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from server.file_events import file_event_broker
from server.git_ignore import ignored_names
from server.paths import (
    ALWAYS_HIDDEN_DIRS,
    EXT_TO_LANG,
    MAX_EDITABLE_BYTES,
    RECURSIVE_SKIP_DIRS,
    is_within_root,
    project_root,
    resolve_within_root,
)

router = APIRouter()


class WriteRequest(BaseModel):
    """写文件请求体。

    base_mtime/base_size：打开文件时记录的基线，保存时用于乐观锁校验；
    两者都缺省时视为强制覆盖，跳过一致性检查。
    """

    path: str
    content: str
    base_mtime: int | None = None
    base_size: int | None = None


class CreateRequest(BaseModel):
    """新建文件/目录请求体。"""

    path: str
    type: str


def _mark_ignored(root: str, items: list[dict]) -> None:
    """给被 git 忽略的条目打上 ignored 标记。

    目录带尾斜杠问：`.venv`、`.mypy_cache` 这类目录是靠自身内部的忽略规则
    「自己忽略自己」，不带斜杠时 git 不认，带上才能与 git status --ignored 的口径
    一致。只给命中的写字段、未命中的保持字段缺失，省掉逐条 false 的冗余体积；
    忽略判定失败（非 git 仓库等）时什么都不写，条目照常返回。
    """
    queries = [item["path"] + "/" if item["type"] == "dir" else item["path"] for item in items]
    ignored = {path.rstrip("/") for path in ignored_names(root, queries)}
    for item in items:
        if item["path"] in ignored:
            item["ignored"] = True


def _list_dir(target: str, root: str, skip_names: set[str]) -> list[dict]:
    """列单个目录：目录排前面、文件排后面，各自按名字排序。

    skip_names 里的名字一律跳过，判断只看名字、不区分目录与文件——`.git` 在
    worktree / submodule 场景下是个文件，只判目录会漏掉它。忽略标记由调用方
    统一补（见 _mark_ignored），避免递归列举时每层目录都去问一次 git。
    """
    dirs: list[dict] = []
    files: list[dict] = []
    for name in os.listdir(target):
        if name in skip_names:
            continue
        full = os.path.join(target, name)
        rel = os.path.relpath(full, root).replace("\\", "/")
        if os.path.isdir(full):
            dirs.append({"name": name, "type": "dir", "path": rel})
        else:
            files.append({"name": name, "type": "file", "path": rel})

    dirs.sort(key=lambda x: x["name"])
    files.sort(key=lambda x: x["name"])
    return dirs + files


def _flatten(items: list[dict]) -> list[dict]:
    """把递归树摊平成条目列表，供一次性打忽略标记用。"""
    flat: list[dict] = []
    for item in items:
        flat.append(item)
        flat.extend(_flatten(item.get("children") or []))
    return flat


@router.get("/api/files/list")
def list_files(path: str = ".", recursive: bool = False) -> dict:
    """列目录接口。

    参数 path：相对路径，默认 "."（项目根目录）。
    参数 recursive：True 时一次性递归返回嵌套树（目录带 children），
    供文件树过滤等需要整棵树视角的场景使用；条目总量设上限防超大仓库。
    返回 {"items": [{"name", "type", "path", "children"?, "ignored"?}]}，
    目录排前面、文件排后面，各自按名字排序。
    只有 .git 目录会被跳过；被 git 忽略的条目照常列出并带 ignored 标记。
    递归模式额外跳过依赖/缓存/构建产物目录（列表见 paths.RECURSIVE_SKIP_DIRS），
    否则条目上限会被这些目录吃满，搜索与快速打开随之失效。
    """
    root = project_root()
    target = os.path.normpath(os.path.join(root, path))

    # 路径安全检查：不允许穿越到项目根之外
    if not is_within_root(target, root):
        return {"items": []}

    if not os.path.isdir(target):
        return {"items": []}

    items = _list_dir(target, root, ALWAYS_HIDDEN_DIRS if not recursive else RECURSIVE_SKIP_DIRS)
    if not recursive:
        _mark_ignored(root, items)
        return {"items": items}

    # 递归模式：广度优先展开所有子目录，目录条目补 children 字段
    MAX_ENTRIES = 20000
    total = len(items)
    queue: list[dict] = [it for it in items if it["type"] == "dir"]
    while queue and total < MAX_ENTRIES:
        item = queue.pop(0)
        full = os.path.join(root, item["path"])
        children = _list_dir(full, root, RECURSIVE_SKIP_DIRS)
        item["children"] = children
        total += len(children)
        queue.extend(c for c in children if c["type"] == "dir")

    # 整棵树只判一次忽略：逐层判会让每层目录各起一个 git 进程
    _mark_ignored(root, _flatten(items))
    return {"items": items}


@router.get("/api/files/read")
def read_file(path: str) -> Any:
    """读文件内容接口。

    参数 path：相对路径。
    返回 {"content": "...", "language": "...", "mtime": int, "size": int, "editable": bool}。
    文件不存在返回 404，路径穿越返回 403。
    """
    root = project_root()
    target = os.path.normpath(os.path.join(root, path))

    # 路径安全检查：不允许 .. 路径穿越
    if not is_within_root(target, root):
        return JSONResponse(status_code=403, content={"error": "path traversal denied"})

    if not os.path.isfile(target):
        return JSONResponse(status_code=404, content={"error": "file not found"})

    try:
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return JSONResponse(status_code=500, content={"error": "read failed"})

    # 一致性基线：整数秒 mtime + size；editable 依据是否超统一可编辑上限
    st = os.stat(target)
    size = st.st_size
    ext = os.path.splitext(path)[1].lower()
    language = EXT_TO_LANG.get(ext, "plaintext")
    return {
        "content": content,
        "language": language,
        "mtime": int(st.st_mtime),
        "size": size,
        "editable": size <= MAX_EDITABLE_BYTES,
    }


@router.post("/api/files/write")
def write_file(req: WriteRequest) -> Any:
    """写文件接口（乐观锁 + 原子写）。

    参数：path 相对路径、content 完整内容、base_mtime/base_size 可选基线。
    返回 {"path", "mtime", "size"}。
    带基线且磁盘 mtime/size 不一致返回 409；路径穿越/软链接穿越返回 403；
    目标不存在返回 404；目标是目录返回 400；超大小上限返回 413。
    """
    # 路径沙箱：软链接展开后校验（与 AI 工具沙箱对齐）
    try:
        target = resolve_within_root(req.path)
    except ValueError:
        return JSONResponse(status_code=403, content={"error": "path traversal denied"})

    if os.path.isdir(target):
        return JSONResponse(status_code=400, content={"error": "target is a directory"})
    # write 不自动创建目录，文件不存在（含被删除）一律 404
    if not os.path.isfile(target):
        return JSONResponse(status_code=404, content={"error": "file not found"})

    # 大小护栏：先编码，捕获非法 UTF-8（如孤立代理项）
    try:
        size = len(req.content.encode("utf-8"))
    except UnicodeEncodeError:
        return JSONResponse(status_code=400, content={"error": "content is not valid utf-8"})
    if size > MAX_EDITABLE_BYTES:
        return JSONResponse(status_code=413, content={"error": "file too large"})

    # 乐观锁：带基线时比对磁盘 mtime/size，任一不一致返回 409
    if req.base_mtime is not None or req.base_size is not None:
        st = os.stat(target)
        cur_mtime = int(st.st_mtime)
        cur_size = st.st_size
        mtime_changed = req.base_mtime is not None and req.base_mtime != cur_mtime
        size_changed = req.base_size is not None and req.base_size != cur_size
        if mtime_changed or size_changed:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "file_modified",
                    "current_mtime": cur_mtime,
                    "current_size": cur_size,
                },
            )

    # 原子写：临时文件建在目标同目录（同文件系统），替换前恢复原文件权限
    orig_mode = os.stat(target).st_mode
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".write-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(req.content)
        os.chmod(tmp_path, orig_mode)
        os.replace(tmp_path, target)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return JSONResponse(status_code=500, content={"error": "write failed"})

    st = os.stat(target)
    return {"path": req.path, "mtime": int(st.st_mtime), "size": st.st_size}


@router.post("/api/files/create")
def create_file(req: CreateRequest) -> Any:
    """新建文件/目录接口。

    参数：path 相对路径、type "file"|"dir"。
    返回 {"path", "type"}。已存在返回 409，越界（含软链接）返回 403，非法 type 返回 400。
    缺失父目录自动创建（限制在沙箱内）。
    """
    if req.type not in ("file", "dir"):
        return JSONResponse(status_code=400, content={"error": "invalid type"})

    # 路径沙箱：软链接展开后校验
    try:
        target = resolve_within_root(req.path)
    except ValueError:
        return JSONResponse(status_code=403, content={"error": "path traversal denied"})

    if os.path.exists(target):
        return JSONResponse(status_code=409, content={"error": "already exists"})

    try:
        if req.type == "dir":
            os.makedirs(target, exist_ok=True)
        else:
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8"):
                pass
    except OSError:
        return JSONResponse(status_code=500, content={"error": "create failed"})

    return {"path": req.path, "type": req.type}


async def file_event_stream():
    """SSE 事件生成器：订阅文件变更事件，队列空时推心跳保活。"""
    queue = file_event_broker.subscribe()
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
    finally:
        file_event_broker.unsubscribe(queue)


@router.get("/api/files/events")
async def file_events() -> StreamingResponse:
    """文件变更事件 SSE 通道。

    AI 工具写盘后，file_events.notify_file_changed 会广播 file_changed 事件，
    前端经此通道接收，用于刷新文件树与标记打开文件的过期状态。
    """
    return StreamingResponse(file_event_stream(), media_type="text/event-stream")
