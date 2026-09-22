"""files 路由测试：读/写/新建接口的正常与异常路径，列目录的可见口径与忽略标记。"""

from __future__ import annotations

import os
import subprocess

import pytest

from server.routers.files.routes import (
    CreateRequest,
    WriteRequest,
    create_file,
    list_files,
    read_file,
    write_file,
)


def _init_repo(path) -> None:
    """把工作区变成 git 仓库，并让约定的忽略规则生效。"""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)


def _names(result: dict) -> list[str]:
    """取一次列目录里所有条目的名字。"""
    return [item["name"] for item in result["items"]]


def _by_name(items: list[dict]) -> dict[str, dict]:
    """按名字建索引，便于断言某个条目的字段。"""
    return {item["name"]: item for item in items}


def _flatten_names(items: list[dict]) -> list[dict]:
    """把递归树摊平，便于断言任意层级里的名字。"""
    flat: list[dict] = []
    for item in items:
        flat.append(item)
        flat.extend(_flatten_names(item.get("children") or []))
    return flat


@pytest.fixture
def git_config_isolated(monkeypatch, tmp_path):
    """隔离全局/系统 git 配置，避免开发机的全局忽略规则干扰忽略断言。"""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))


@pytest.mark.asyncio
async def test_read_returns_baseline(workspace):
    content = "print('hi')\n"
    (workspace / "a.py").write_text(content, encoding="utf-8")
    result = read_file("a.py")
    assert result["content"] == content
    assert result["language"] == "python"
    assert result["editable"] is True
    assert result["mtime"] == int(os.stat(workspace / "a.py").st_mtime)
    assert result["size"] == os.stat(workspace / "a.py").st_size


@pytest.mark.asyncio
async def test_read_path_traversal_403(workspace):
    result = read_file("../secret.txt")
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_read_not_found_404(workspace):
    result = read_file("missing.txt")
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_write_success(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    st = os.stat(workspace / "a.py")
    result = write_file(
        WriteRequest(path="a.py", content="new", base_mtime=int(st.st_mtime), base_size=st.st_size)
    )
    assert result["size"] == len("new".encode("utf-8"))
    assert (workspace / "a.py").read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_conflict_409(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    # 传入错误基线制造冲突
    result = write_file(WriteRequest(path="a.py", content="new", base_mtime=0, base_size=0))
    assert result.status_code == 409
    # 未被覆盖
    assert (workspace / "a.py").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_write_force_no_baseline(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    result = write_file(WriteRequest(path="a.py", content="new"))
    assert result["size"] == len("new".encode("utf-8"))


@pytest.mark.asyncio
async def test_write_path_traversal_403(workspace):
    result = write_file(WriteRequest(path="../x.py", content="x"))
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_write_symlink_traversal_403(workspace, tmp_path):
    # 软链接指向工作区外，写入应被拒绝（软链接展开后越界）
    outside = tmp_path.parent / "escape_outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.py"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前环境不支持创建软链接")
    try:
        result = write_file(WriteRequest(path="link.py", content="x"))
        assert result.status_code == 403
    finally:
        link.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_write_nonexistent_404(workspace):
    result = write_file(WriteRequest(path="missing.py", content="x"))
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_write_directory_400(workspace):
    (workspace / "d").mkdir()
    result = write_file(WriteRequest(path="d", content="x"))
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_create_file_and_dir(workspace):
    create_file(CreateRequest(path="new.txt", type="file"))
    assert (workspace / "new.txt").is_file()
    create_file(CreateRequest(path="sub/dir", type="dir"))
    assert (workspace / "sub" / "dir").is_dir()


@pytest.mark.asyncio
async def test_create_already_exists_409(workspace):
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    result = create_file(CreateRequest(path="a.txt", type="file"))
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_create_invalid_type_400(workspace):
    result = create_file(CreateRequest(path="x", type="link"))
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_list_recursive_returns_nested_tree(workspace):
    (workspace / "sub").mkdir()
    (workspace / "sub" / "deep").mkdir()
    (workspace / "sub" / "a.py").write_text("x", encoding="utf-8")
    (workspace / "sub" / "deep" / "b.py").write_text("y", encoding="utf-8")
    (workspace / "top.py").write_text("z", encoding="utf-8")
    result = list_files(".", recursive=True)
    items = {it["name"]: it for it in result["items"]}
    assert "top.py" in items
    sub = items["sub"]
    assert sub["type"] == "dir"
    sub_children = {c["name"]: c for c in sub["children"]}
    assert "a.py" in sub_children
    assert [c["name"] for c in sub_children["deep"]["children"]] == ["b.py"]


@pytest.mark.asyncio
async def test_list_recursive_off_keeps_flat(workspace):
    (workspace / "sub").mkdir()
    result = list_files(".", recursive=False)
    for it in result["items"]:
        assert "children" not in it


@pytest.mark.asyncio
async def test_list_shows_dot_entries_but_not_git(workspace):
    """点开头的条目照常列出，只有 .git 仍然不出现（目录形态与文件形态都不列）。"""
    (workspace / ".env").write_text("K=1", encoding="utf-8")
    (workspace / ".agent" / "specs").mkdir(parents=True)
    (workspace / ".agent" / "specs" / "spec.md").write_text("x", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    # worktree / submodule 下 .git 是指针文件，跳过判断只看名字才能一并盖住
    (workspace / "sub").mkdir()
    (workspace / "sub" / ".git").write_text("gitdir: ../.git/modules/sub", encoding="utf-8")

    names = _names(list_files("."))

    assert ".env" in names
    assert ".agent" in names
    assert ".git" not in names
    assert ".git" not in _names(list_files("sub"))
    # 展开点开头目录能看到它下面的文件
    assert _names(list_files(".agent")) == ["specs"]
    assert _names(list_files(".agent/specs")) == ["spec.md"]
    # 递归树的任何一层都不出现 .git
    recursive = list_files(".", recursive=True)["items"]
    flat_names = [it["name"] for it in _flatten_names(recursive)]
    assert flat_names and ".git" not in flat_names


@pytest.mark.asyncio
async def test_list_recursive_skips_heavy_dirs_keeps_dot_dirs(workspace):
    """递归列举跳过依赖/缓存目录，但不跳过 .agent 这类点开头目录。"""
    (workspace / "node_modules" / "pkg").mkdir(parents=True)
    (workspace / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (workspace / ".venv").mkdir()
    (workspace / ".agent" / "specs").mkdir(parents=True)
    (workspace / ".agent" / "specs" / "spec.md").write_text("x", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("x", encoding="utf-8")

    items = list_files(".", recursive=True)["items"]
    names = _names({"items": items})

    assert "node_modules" not in names
    assert ".venv" not in names
    assert "src" in names
    # 点开头目录下的文件仍能通过递归树取到，搜索与快速打开才有得命中
    agent = _by_name(items)[".agent"]
    assert _by_name(agent["children"])["specs"]["children"][0]["name"] == "spec.md"


@pytest.mark.asyncio
async def test_list_marks_gitignored_entries(workspace, git_config_isolated):
    """被 .gitignore 命中的条目带 ignored 标记，未命中的不带字段。"""
    _init_repo(workspace)
    (workspace / ".gitignore").write_text(".env\nbuild/\n", encoding="utf-8")
    (workspace / ".env").write_text("K=1", encoding="utf-8")
    (workspace / "build").mkdir()
    (workspace / "build" / "out.js").write_text("x", encoding="utf-8")
    (workspace / "app.py").write_text("x", encoding="utf-8")

    items = _by_name(list_files(".")["items"])

    assert items[".env"]["ignored"] is True
    assert items["build"]["ignored"] is True
    assert "ignored" not in items["app.py"]
    # 递归树同样带标记（供过滤结果树灰显）
    recursive = _by_name(list_files(".", recursive=True)["items"])
    assert recursive[".env"]["ignored"] is True
    assert "ignored" not in recursive["app.py"]


@pytest.mark.asyncio
async def test_list_marks_self_ignoring_dir(workspace, git_config_isolated):
    """.venv 这类靠自己内部规则忽略自己的目录也要被判为忽略。"""
    _init_repo(workspace)
    (workspace / ".gitignore").write_text("", encoding="utf-8")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / ".gitignore").write_text("*\n", encoding="utf-8")
    (workspace / ".venv" / "pyvenv.cfg").write_text("x", encoding="utf-8")

    items = _by_name(list_files(".")["items"])

    assert items[".venv"]["ignored"] is True


@pytest.mark.asyncio
async def test_list_without_git_repo_marks_nothing(workspace):
    """非 git 工作区不报错：点开头条目照常列出，全部没有 ignored 标记。"""
    (workspace / ".env").write_text("K=1", encoding="utf-8")

    items = list_files(".")["items"]

    assert ".env" in _names({"items": items})
    assert all("ignored" not in item for item in items)
