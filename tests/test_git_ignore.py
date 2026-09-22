"""git_ignore 测试：批量忽略判定与各类降级路径。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server.git_ignore import ignored_names


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


@pytest.fixture(autouse=True)
def _isolate_git_config(monkeypatch, tmp_path):
    """隔离全局/系统 git 配置：开发机的全局忽略规则会干扰「未命中」断言。"""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))


@pytest.fixture
def repo(tmp_path):
    """建一个最小 git 仓库（带 .gitignore），返回仓库根目录。"""
    root = tmp_path / "repo"
    root.mkdir()
    _run_git(root, ["init"])
    (root / ".gitignore").write_text("*.log\nbuild/\n临时/\n", encoding="utf-8")
    return root


def test_ignored_names_hits_and_misses(repo):
    """命中的返回、未命中的不返回。"""
    (repo / "app.log").write_text("x", encoding="utf-8")
    (repo / "app.py").write_text("x", encoding="utf-8")

    result = ignored_names(str(repo), ["app.log", "app.py", "missing.log"])

    # missing.log 磁盘上不存在，但忽略规则只看路径，照样命中
    assert result == {"app.log", "missing.log"}


def test_ignored_names_directory_needs_trailing_slash(repo):
    """目录带尾斜杠才按目录口径命中的场景：build/ 规则只对目录生效。"""
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("x", encoding="utf-8")

    assert ignored_names(str(repo), ["build/"]) == {"build/"}
    # 带斜杠时其下文件按父目录被忽略命中
    assert ignored_names(str(repo), ["build/out.js"]) == {"build/out.js"}


def test_ignored_names_chinese_path(repo):
    """中文路径不乱码：按 utf-8 喂 stdin 与解码输出。"""
    (repo / "临时").mkdir()
    (repo / "临时" / "草稿.md").write_text("x", encoding="utf-8")

    assert ignored_names(str(repo), ["临时/", "临时/草稿.md"]) == {"临时/", "临时/草稿.md"}


def test_ignored_names_skips_tracked_file(repo):
    """判定看索引：被跟踪的文件即使命中忽略模式也不算忽略。"""
    tracked = repo / "keep.log"
    tracked.write_text("x", encoding="utf-8")
    _run_git(repo, ["add", "-f", "keep.log"])

    assert ignored_names(str(repo), ["keep.log"]) == set()


def test_ignored_names_not_a_repo(tmp_path):
    """非 git 目录返回空集，不抛异常。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.log").write_text("x", encoding="utf-8")

    assert ignored_names(str(plain), ["a.log"]) == set()


def test_ignored_names_empty_input_skips_git(monkeypatch):
    """空清单直接返回，不起进程。"""

    def _boom(*args, **kwargs):
        raise AssertionError("空清单不应调用 git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert ignored_names(".", []) == set()


def test_ignored_names_timeout_returns_empty(repo, monkeypatch):
    """超时按「都没被忽略」处理，不影响调用方。"""

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert ignored_names(str(repo), ["app.log"]) == set()


def test_ignored_names_missing_git_returns_empty(repo, monkeypatch):
    """git 不可用（未安装/不在 PATH）时同样按空集降级。"""

    def _missing(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _missing)
    assert ignored_names(str(repo), ["app.log"]) == set()
