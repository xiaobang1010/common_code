"""git 路由测试 - 重点覆盖重写后的 /api/git/diff 前后对比接口。

用真实 git 仓库做夹具：临时目录初始化仓库、提交基线、再制造各类改动，
验证 modified/untracked/deleted/binary/tooLarge 五种返回、路径安全校验、
工作区为仓库子目录的路径换算，以及中文文件名经 status 清单回传后可命中。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server.paths import project_root, set_project_root


def _run_git(cwd: Path, args: list[str]) -> None:
    """在指定目录执行 git 命令，失败直接让测试报错。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"


@pytest.fixture
def git_env(tmp_path):
    """git 仓库工厂：按给定布局建仓库并做初始提交，把工作区根切过去。

    返回工厂函数 _make(layout, workspace_rel)，layout 是 {相对路径: 初始内容}，
    workspace_rel 指定工作区根在仓库内的相对位置（空串表示就是仓库根）。
    结束后恢复原工作区根。
    """
    original = project_root()

    def _make(layout: dict[str, str], workspace_rel: str = "") -> tuple[Path, Path]:
        repo = tmp_path / "repo"
        repo.mkdir()
        _run_git(repo, ["init"])
        _run_git(repo, ["config", "user.name", "tester"])
        _run_git(repo, ["config", "user.email", "tester@example.com"])
        for rel, content in layout.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _run_git(repo, ["add", "."])
        # 空布局时也要有 HEAD 可查，允许空提交
        _run_git(repo, ["commit", "--allow-empty", "-m", "init"])
        workspace = repo / workspace_rel if workspace_rel else repo
        set_project_root(str(workspace))
        return repo, workspace

    yield _make
    set_project_root(original)


def test_diff_modified_file(git_env):
    """已跟踪文件的修改：旧文为新内容对比，行数统计来自 numstat。"""
    from server.routers.git.routes import git_diff

    _, ws = git_env({"a.txt": "line1\nline2\n"})
    (ws / "a.txt").write_text("line1\nchanged\nadded\n", encoding="utf-8")

    result = git_diff(path="a.txt")

    assert result["error"] == ""
    assert result["binary"] is False
    assert result["tooLarge"] is False
    assert result["oldText"] == "line1\nline2\n"
    assert result["newText"] == "line1\nchanged\nadded\n"
    assert result["additions"] >= 1
    assert result["deletions"] >= 1


def test_diff_untracked_file(git_env):
    """未跟踪的新文件：oldText 空串全绿新增，新增行数为整文件行数。"""
    _, ws = git_env({})
    (ws / "new.txt").write_text("n1\nn2\nn3\n", encoding="utf-8")

    from server.routers.git.routes import git_diff

    result = git_diff(path="new.txt")

    assert result["error"] == ""
    assert result["oldText"] == ""
    assert result["newText"] == "n1\nn2\nn3\n"
    assert result["additions"] == 3
    assert result["deletions"] == 0


def test_diff_deleted_file(git_env):
    """已删除的跟踪文件：newText 空串全红删除，删除行数来自 numstat。"""
    _, ws = git_env({"gone.txt": "d1\nd2\n"})
    (ws / "gone.txt").unlink()

    from server.routers.git.routes import git_diff

    result = git_diff(path="gone.txt")

    assert result["error"] == ""
    assert result["oldText"] == "d1\nd2\n"
    assert result["newText"] == ""
    assert result["deletions"] == 2


def test_diff_binary_file(git_env):
    """二进制文件（前 8KB 含空字节）：不下发内容，binary 标记为真。"""
    _, ws = git_env({})
    (ws / "blob.bin").write_bytes(b"AB\x00CD")
    _run_git(ws, ["add", "."])
    _run_git(ws, ["commit", "-m", "add bin"])
    (ws / "blob.bin").write_bytes(b"XY\x00ZW")

    from server.routers.git.routes import git_diff

    result = git_diff(path="blob.bin")

    assert result["error"] == ""
    assert result["binary"] is True
    assert result["oldText"] == ""
    assert result["newText"] == ""


def test_diff_too_large_file(git_env):
    """超过 1MB 的文件：不下发内容，tooLarge 标记为真。"""
    _, ws = git_env({"big.txt": "seed\n"})
    (ws / "big.txt").write_bytes(b"A" * (1024 * 1024 + 1024))

    from server.routers.git.routes import git_diff

    result = git_diff(path="big.txt")

    assert result["error"] == ""
    assert result["tooLarge"] is True
    assert result["oldText"] == ""
    assert result["newText"] == ""


def test_diff_missing_everywhere(git_env):
    """HEAD 和磁盘上都不存在的路径：报 file not found。"""
    git_env({})

    from server.routers.git.routes import git_diff

    result = git_diff(path="nope.txt")

    assert result["error"] == "file not found"


def test_diff_rejects_bad_paths(git_env, tmp_path):
    """穿越路径、工作区外绝对路径、空路径均被拒绝且不下发内容。"""
    _, ws = git_env({"a.txt": "x\n"})
    from server.routers.git.routes import git_diff

    traversal = git_diff(path="../escape.txt")
    assert traversal["error"] == "path outside workspace"

    outside = tmp_path / "elsewhere.txt"
    outside.write_text("secret\n", encoding="utf-8")
    absolute = git_diff(path=str(outside))
    assert absolute["error"] == "path outside workspace"

    empty = git_diff(path="")
    assert empty["error"] == "path is required"


def test_diff_workspace_is_repo_subdir(git_env):
    """工作区是仓库子目录：兄弟目录文件拒绝，子目录内文件换算正确。"""
    repo, ws = git_env(
        {"sub/a.txt": "s1\ns2\n", "sibling.txt": "root file\n"},
        workspace_rel="sub",
    )
    from server.routers.git.routes import git_diff, git_status

    # 仓库内但工作区外的文件（兄弟目录）同样拒绝
    (repo / "sibling.txt").write_text("root file changed\n", encoding="utf-8")
    denied = git_diff(path="sibling.txt")
    assert denied["error"] == "path outside workspace"

    # 工作区内的文件正常出对比，验证 show-toplevel 路径换算正确
    (ws / "a.txt").write_text("s1\ns2-edited\n", encoding="utf-8")
    ok = git_diff(path="sub/a.txt")
    assert ok["error"] == ""
    assert ok["oldText"] == "s1\ns2\n"
    assert ok["newText"] == "s1\ns2-edited\n"

    # 子目录工作区 + 未跟踪新文件：行数统计按整文件行数，不被路径基准错位
    (ws / "new.txt").write_text("n1\nn2\nn3\n", encoding="utf-8")
    untracked = git_diff(path="sub/new.txt")
    assert untracked["error"] == ""
    assert untracked["oldText"] == ""
    assert untracked["newText"] == "n1\nn2\nn3\n"
    assert untracked["additions"] == 3
    status = git_status()
    untracked_row = next(
        (c for c in status["changes"] if c["path"] == "sub/new.txt"), None
    )
    assert untracked_row is not None
    assert untracked_row["additions"] == 3


def test_diff_chinese_filename_via_status(git_env):
    """中文文件名：status 清单输出原名（quotepath=false），diff 可命中。"""
    _, ws = git_env({"说明文档.md": "第一版\n"})
    (ws / "说明文档.md").write_text("第一版\n第二版\n", encoding="utf-8")

    from server.routers.git.routes import git_diff, git_status

    status = git_status()
    paths = [change["path"] for change in status["changes"]]
    assert "说明文档.md" in paths

    result = git_diff(path="说明文档.md")
    assert result["error"] == ""
    assert result["oldText"] == "第一版\n"
    assert "第二版" in result["newText"]
