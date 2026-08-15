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
from server.paths import (
    EXT_TO_LANG,
    EXCLUDED_DIRS,
    MAX_EDITABLE_BYTES,
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


def _list_dir(target: str, root: str) -> list[dict]:
    """列单个目录：目录排前面、文件排后面，各自按名字排序，隐藏文件与排除目录跳过。"""
    dirs: list[dict] = []
    files: list[dict] = []
    for name in os.listdir(target):
        # 排除隐藏文件
        if name.startswith("."):
            continue
        full = os.path.join(target, name)
        rel = os.path.relpath(full, root).replace("\\", "/")
        if os.path.isdir(full):
            if name in EXCLUDED_DIRS:
                continue
            dirs.append({"name": name, "type": "dir", "path": rel})
        else:
            files.append({"name": name, "type": "file", "path": rel})

    dirs.sort(key=lambda x: x["name"])
    files.sort(key=lambda x: x["name"])
    return dirs + files


@router.get("/api/files/list")
async def list_files(path: str = ".", recursive: bool = False) -> dict:
    """列目录接口。

    参数 path：相对路径，默认 "."（项目根目录）。
    参数 recursive：True 时一次性递归返回嵌套树（目录带 children），
    供文件树过滤等需要整棵树视角的场景使用；条目总量设上限防超大仓库。
    返回 {"items": [{"name", "type", "path", "children"?}]}，
    目录排前面、文件排后面，各自按名字排序。
    隐藏文件和指定目录会被排除。
    """
    root = project_root()
    target = os.path.normpath(os.path.join(root, path))

    # 路径安全检查：不允许穿越到项目根之外
    if not is_within_root(target, root):
        return {"items": []}

    if not os.path.isdir(target):
        return {"items": []}

    items = _list_dir(target, root)
    if not recursive:
        return {"items": items}

    # 递归模式：广度优先展开所有子目录，目录条目补 children 字段
    MAX_ENTRIES = 20000
    total = len(items)
    queue: list[dict] = [it for it in items if it["type"] == "dir"]
    while queue and total < MAX_ENTRIES:
        item = queue.pop(0)
        full = os.path.join(root, item["path"])
        children = _list_dir(full, root)
        item["children"] = children
        total += len(children)
        queue.extend(c for c in children if c["type"] == "dir")
    return {"items": items}


@router.get("/api/files/read")
async def read_file(path: str) -> Any:
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
async def write_file(req: WriteRequest) -> Any:
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
async def create_file(req: CreateRequest) -> Any:
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
