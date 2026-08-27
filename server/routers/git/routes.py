"""Git 相关路由：状态、暂存、提交、diff、分支。"""

from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter

from server.paths import is_within_root, project_root

router = APIRouter()

# 统一的 git 全局参数：关闭路径转义，中文文件名原样输出（配 utf-8 解码）
GIT_GLOBAL_ARGS = ["-c", "core.quotepath=false"]
# 统一的子进程文本解码选项：git 输出为 utf-8 字节，Windows 默认本地编码会乱码
GIT_TEXT_OPTS = {"encoding": "utf-8", "errors": "replace"}

# diff 单侧内容超过该字节数时不下发全文，前端用占位提示代替对比视图
MAX_DIFF_BYTES = 1024 * 1024


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
    # 未跟踪文件：X 列为问号
    untracked = x == "?"
    # 暂存区状态 X：空格或问号表示无暂存改动
    if x not in (" ", "?"):
        changes.append(
            {"path": file_path, "status": status_map.get(x, "unknown"), "staged": True}
        )
    # 工作区状态 Y：空格表示无未暂存改动
    if y != " ":
        changes.append(
            {
                "path": file_path,
                "status": status_map.get(y, "unknown"),
                "staged": False,
                "untracked": untracked,
            }
        )
    return changes


def _numstat_stats(root: str) -> dict[str, tuple[int, int]]:
    """执行 git diff HEAD --numstat，返回 {路径: (新增行数, 删除行数)}。

    对比 HEAD 同时覆盖已暂存与未暂存的改动；二进制文件无行数统计按 0 处理。
    执行失败或不在 git 仓库时返回空字典。
    """
    try:
        proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "diff", "HEAD", "--numstat"],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return {}
    if proc.returncode != 0:
        return {}

    stats: dict[str, tuple[int, int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        # 路径可能含制表符，剩余部分拼回
        path = "\t".join(parts[2:])
        try:
            adds = int(parts[0])
        except ValueError:
            # 二进制文件输出 "-"，按 0 处理
            adds = 0
        try:
            dels = int(parts[1])
        except ValueError:
            dels = 0
        stats[path] = (adds, dels)
    return stats


def _count_file_lines(abs_path: str) -> int:
    """统计未跟踪文件的总行数，作为新增行数统计。读取失败返回 0。

    abs_path 必须是已解析的绝对路径：porcelain 输出的路径是仓库根相对
    口径，工作区可能是仓库子目录，直接用工作区根 join 会落点错位。
    """
    try:
        with open(abs_path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


@router.get("/api/git/status")
def git_status() -> dict:
    """Git 状态接口。

    返回 {"branch": "...", "changes": [{"path", "status", "staged", "additions", "deletions"}],
    "totals": {"files", "additions", "deletions"}, "repo_prefix": "..."}。
    其中 staged 为 True 表示已暂存，False 表示未暂存；
    additions/deletions 为该文件的变更行数统计（未跟踪文件按文件总行数计新增）；
    totals 按去重后的文件路径汇总；
    repo_prefix 为工作区相对仓库根的路径前缀（正斜杠口径，工作区即仓库根时为空串），
    供前端把仓库根相对的 changes[].path 归一成工作区相对口径。
    不在 git 仓库或调用失败时返回空分支和空变更列表。
    """
    root = project_root()

    try:
        branch_proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "branch", "--show-current"],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=5,
        )
        branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""

        status_proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=5,
        )

        changes: list[dict] = []
        if status_proc.returncode == 0:
            for line in status_proc.stdout.splitlines():
                parsed = _parse_porcelain_line(line)
                if parsed:
                    changes.extend(parsed)

        # 仓库根定位：未跟踪文件路径与 repo_prefix 都按仓库根口径计算
        toplevel = _repo_toplevel(root)
        rel_root = os.path.relpath(root, toplevel) if toplevel else "."
        repo_prefix = "" if rel_root == "." else rel_root.replace(os.sep, "/")

        # 逐文件行数统计：已跟踪文件用 numstat，未跟踪文件数总行数
        stats = _numstat_stats(root)
        seen_paths: set[str] = set()
        total_adds = 0
        total_dels = 0
        for change in changes:
            path = change["path"]
            if change.get("untracked"):
                abs_path = os.path.join(root, path)
                if not os.path.isfile(abs_path) and toplevel:
                    abs_path = os.path.join(toplevel, path)
                adds = _count_file_lines(abs_path)
                dels = 0
            else:
                adds, dels = stats.get(path, (0, 0))
            change["additions"] = adds
            change["deletions"] = dels
            # 同一文件可能同时有暂存/未暂存两项，总计只算一次；
            # 新增目录（路径以 / 结尾）不计入文件数
            if path not in seen_paths and not path.endswith("/"):
                seen_paths.add(path)
                total_adds += adds
                total_dels += dels

        return {
            "branch": branch,
            "changes": changes,
            "totals": {
                "files": len(seen_paths),
                "additions": total_adds,
                "deletions": total_dels,
            },
            "repo_prefix": repo_prefix,
        }
    except (subprocess.SubprocessError, OSError):
        return {"branch": "", "changes": [], "repo_prefix": ""}


@router.post("/api/git/stage")
def git_stage(body: dict) -> dict:
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
            ["git", *GIT_GLOBAL_ARGS, "add", path],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True}


@router.post("/api/git/unstage")
def git_unstage(body: dict) -> dict:
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
            ["git", *GIT_GLOBAL_ARGS, "reset", "HEAD", path],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True}


@router.post("/api/git/commit")
def git_commit(body: dict) -> dict:
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
            ["git", *GIT_GLOBAL_ARGS, "commit", "-m", message],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True}


def _repo_toplevel(root: str) -> str:
    """返回工作区所在 git 仓库的根目录，不在仓库内时返回空串。"""
    try:
        proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _load_side_content(data: bytes) -> tuple[str, bool]:
    """把单侧原始字节转成文本，返回 (文本内容, 是否二进制)。

    前 8KB 出现空字节即判定为二进制，文本侧不做解码。换行统一归一为 LF：
    Windows 下 core.autocrlf 会让磁盘是 CRLF 而 HEAD 是 LF，不归一会导致
    整个文件在对比视图里被标红。
    """
    if b"\0" in data[:8192]:
        return "", True
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n"), False


def _git_show_head(toplevel: str, rel_path: str) -> bytes | None:
    """取 HEAD 版本的文件原始字节，取不到（未跟踪等）返回 None。"""
    try:
        proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "show", f"HEAD:{rel_path}"],
            cwd=toplevel,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _diff_payload(path: str, error: str = "") -> dict:
    """构造空的 diff 结果结构，error 非空表示本次未能取得对比内容。"""
    return {
        "path": path,
        "oldText": "",
        "newText": "",
        "binary": False,
        "tooLarge": False,
        "additions": 0,
        "deletions": 0,
        "error": error,
    }


@router.get("/api/git/diff")
def git_diff(path: str = "") -> dict:
    """获取单个文件前后对比内容的接口（HEAD 版本 vs 工作区当前版本）。

    参数 path：仓库根相对路径，与 /api/git/status 变更清单的口径一致。
    返回 {"path", "oldText", "newText", "binary", "tooLarge", "additions",
    "deletions", "error"}。未跟踪的新文件 oldText 为空串（全绿新增），
    已删除文件 newText 为空串（全红删除）；二进制与超过 1MB 的文件不下发
    内容，由前端展示占位提示。路径越出工作区、不在 git 仓库等情况在
    error 中说明，内容字段为空。
    """
    root = project_root()
    if not path or path.endswith("/"):
        return _diff_payload(path, error="path is required")

    toplevel = _repo_toplevel(root)
    if not toplevel:
        return _diff_payload(path, error="not a git repository")

    # path 是仓库根相对口径：先落到仓库根上展开 realpath，再要求落点仍在
    # 当前工作区内；「仓库内但工作区外」（如工作区是仓库子目录时的兄弟目录）
    # 同样拒绝，安全边界始终是工作区
    candidate = os.path.realpath(os.path.join(toplevel, path))
    if not is_within_root(candidate, os.path.realpath(root)):
        return _diff_payload(path, error="path outside workspace")

    new_text = ""
    old_text = ""
    binary = False
    too_large = False

    # 新侧：工作区磁盘上的当前内容；文件已删除时保持空串即全红删除
    if os.path.isfile(candidate):
        try:
            if os.path.getsize(candidate) > MAX_DIFF_BYTES:
                too_large = True
            else:
                with open(candidate, "rb") as f:
                    text, is_bin = _load_side_content(f.read())
                if is_bin:
                    binary = True
                else:
                    new_text = text
        except OSError:
            return _diff_payload(path, error="cannot read file")

    head_data = _git_show_head(toplevel, path)
    if head_data is None and not os.path.isfile(candidate):
        # HEAD 里没有、磁盘上也没有，说明路径本身无效
        return _diff_payload(path, error="file not found")

    # 旧侧：HEAD 版本内容；未跟踪的新文件取不到，保持空串即全绿新增。
    # 任一侧已判定二进制/超大时不再解码另一侧
    if head_data is not None and not binary and not too_large:
        if len(head_data) > MAX_DIFF_BYTES:
            too_large = True
        else:
            text, is_bin = _load_side_content(head_data)
            if is_bin:
                binary = True
            else:
                old_text = text

    # 行数统计与 status 清单同口径：普通文件走 numstat，未跟踪按整文件行数计新增。
    # candidate 已是仓库根相对口径解析后的绝对路径（工作区为子目录时也能命中）
    if head_data is None and new_text:
        adds = _count_file_lines(candidate)
        dels = 0
    else:
        adds, dels = _numstat_stats(root).get(path, (0, 0))

    return {
        "path": path,
        "oldText": old_text,
        "newText": new_text,
        "binary": binary,
        "tooLarge": too_large,
        "additions": adds,
        "deletions": dels,
        "error": "",
    }


@router.get("/api/git/branches")
def git_branches(path: str = "") -> dict:
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
            ["git", *GIT_GLOBAL_ARGS, "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=5,
        )
        if cur_proc.returncode == 0:
            current = cur_proc.stdout.strip()

        # 获取所有分支
        list_proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "branch"],
            cwd=cwd,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
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
def git_checkout(body: dict) -> dict:
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
            ["git", *GIT_GLOBAL_ARGS, "checkout", branch],
            cwd=root,
            capture_output=True,
            text=True,
            **GIT_TEXT_OPTS,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": str(e)}

    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}

    return {"ok": True, "branch": branch}
