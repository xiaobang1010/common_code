"""Git 相关路由：状态、暂存、提交、diff、分支。"""

from __future__ import annotations

import subprocess

from fastapi import APIRouter

from server.paths import project_root

router = APIRouter()


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


@router.get("/api/git/status")
async def git_status() -> dict:
    """Git 状态接口。

    返回 {"branch": "...", "changes": [{"path", "status", "staged"}]}。
    其中 staged 为 True 表示已暂存，False 表示未暂存。
    不在 git 仓库或调用失败时返回空分支和空变更列表。
    """
    root = project_root()

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


@router.post("/api/git/stage")
async def git_stage(body: dict) -> dict:
    """暂存文件接口，执行 git add。

    请求体：{"path": "..."}
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    root = project_root()
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


@router.post("/api/git/unstage")
async def git_unstage(body: dict) -> dict:
    """取消暂存接口，执行 git reset HEAD。

    请求体：{"path": "..."}
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    path = body.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    root = project_root()
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


@router.post("/api/git/commit")
async def git_commit(body: dict) -> dict:
    """提交接口，执行 git commit -m。

    请求体：{"message": "..."}
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    message = body.get("message", "")
    if not message:
        return {"ok": False, "error": "message is required"}
    root = project_root()
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


@router.get("/api/git/diff")
async def git_diff(path: str = "") -> dict:
    """获取文件 diff 接口，执行 git diff。

    参数 path：文件路径，可选。
    返回 {"diff": "..."}。
    """
    root = project_root()
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


@router.get("/api/git/branches")
async def git_branches(path: str = "") -> dict:
    """列出所有 Git 分支。

    参数 path：可选，默认用当前工作区。
    返回 {"branches": [...], "current": "..."}。当前分支排在第一位。
    非 git 仓库返回空列表。
    """
    cwd = path if path else project_root()
    branches: list[str] = []
    current = ""

    try:
        # 获取当前分支
        cur_proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if cur_proc.returncode == 0:
            current = cur_proc.stdout.strip()

        # 获取所有分支
        list_proc = subprocess.run(
            ["git", "branch"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if list_proc.returncode == 0:
            for line in list_proc.stdout.splitlines():
                branch_name = line.strip()
                # 当前分支行以 * 开头
                if branch_name.startswith("* "):
                    branch_name = branch_name[2:].strip()
                if branch_name and branch_name not in branches:
                    branches.append(branch_name)
    except (subprocess.SubprocessError, OSError):
        return {"branches": [], "current": ""}

    # 当前分支排到第一位
    if current and current in branches:
        branches.remove(current)
        branches.insert(0, current)

    return {"branches": branches, "current": current}


@router.post("/api/git/checkout")
async def git_checkout(body: dict) -> dict:
    """切换 Git 分支。

    请求体：{"branch": "..."}
    返回 {"ok": true, "branch": "..."}
    """
    branch = body.get("branch", "")
    if not branch:
        return {"ok": False, "error": "branch is required"}

    root = project_root()
    try:
        proc = subprocess.run(
            ["git", "checkout", branch],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}

    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}

    return {"ok": True, "branch": branch}
