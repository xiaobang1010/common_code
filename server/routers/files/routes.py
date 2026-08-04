"""文件相关路由：列目录、读文件。"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.paths import EXT_TO_LANG, EXCLUDED_DIRS, is_within_root, project_root

router = APIRouter()


@router.get("/api/files/list")
async def list_files(path: str = ".") -> dict:
    """列目录接口。

    参数 path：相对路径，默认 "."（项目根目录）。
    返回 {"items": [{"name", "type", "path"}]}，
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
    return {"items": dirs + files}


@router.get("/api/files/read")
async def read_file(path: str) -> Any:
    """读文件内容接口。

    参数 path：相对路径。
    返回 {"content": "...", "language": "..."}。
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

    ext = os.path.splitext(path)[1].lower()
    language = EXT_TO_LANG.get(ext, "plaintext")
    return {"content": content, "language": language}
