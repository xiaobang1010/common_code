"""files 路由测试：读/写/新建接口的正常与异常路径。"""

from __future__ import annotations

import os

import pytest

from server.routers.files.routes import (
    CreateRequest,
    WriteRequest,
    create_file,
    list_files,
    read_file,
    write_file,
)


@pytest.mark.asyncio
async def test_read_returns_baseline(workspace):
    content = "print('hi')\n"
    (workspace / "a.py").write_text(content, encoding="utf-8")
    result = await read_file("a.py")
    assert result["content"] == content
    assert result["language"] == "python"
    assert result["editable"] is True
    assert result["mtime"] == int(os.stat(workspace / "a.py").st_mtime)
    assert result["size"] == os.stat(workspace / "a.py").st_size


@pytest.mark.asyncio
async def test_read_path_traversal_403(workspace):
    result = await read_file("../secret.txt")
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_read_not_found_404(workspace):
    result = await read_file("missing.txt")
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_write_success(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    st = os.stat(workspace / "a.py")
    result = await write_file(
        WriteRequest(path="a.py", content="new", base_mtime=int(st.st_mtime), base_size=st.st_size)
    )
    assert result["size"] == len("new".encode("utf-8"))
    assert (workspace / "a.py").read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_conflict_409(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    # 传入错误基线制造冲突
    result = await write_file(WriteRequest(path="a.py", content="new", base_mtime=0, base_size=0))
    assert result.status_code == 409
    # 未被覆盖
    assert (workspace / "a.py").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_write_force_no_baseline(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    result = await write_file(WriteRequest(path="a.py", content="new"))
    assert result["size"] == len("new".encode("utf-8"))


@pytest.mark.asyncio
async def test_write_path_traversal_403(workspace):
    result = await write_file(WriteRequest(path="../x.py", content="x"))
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
        result = await write_file(WriteRequest(path="link.py", content="x"))
        assert result.status_code == 403
    finally:
        link.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_write_nonexistent_404(workspace):
    result = await write_file(WriteRequest(path="missing.py", content="x"))
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_write_directory_400(workspace):
    (workspace / "d").mkdir()
    result = await write_file(WriteRequest(path="d", content="x"))
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_create_file_and_dir(workspace):
    await create_file(CreateRequest(path="new.txt", type="file"))
    assert (workspace / "new.txt").is_file()
    await create_file(CreateRequest(path="sub/dir", type="dir"))
    assert (workspace / "sub" / "dir").is_dir()


@pytest.mark.asyncio
async def test_create_already_exists_409(workspace):
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    result = await create_file(CreateRequest(path="a.txt", type="file"))
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_create_invalid_type_400(workspace):
    result = await create_file(CreateRequest(path="x", type="link"))
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_list_recursive_returns_nested_tree(workspace):
    (workspace / "sub").mkdir()
    (workspace / "sub" / "deep").mkdir()
    (workspace / "sub" / "a.py").write_text("x", encoding="utf-8")
    (workspace / "sub" / "deep" / "b.py").write_text("y", encoding="utf-8")
    (workspace / "top.py").write_text("z", encoding="utf-8")
    result = await list_files(".", recursive=True)
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
    result = await list_files(".", recursive=False)
    for it in result["items"]:
        assert "children" not in it
