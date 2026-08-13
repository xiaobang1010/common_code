"""AI 文件工具测试：Read 基线/分段读、Write/Edit 乐观锁与大小护栏。"""

from __future__ import annotations

import os

import pytest

from tools.implementations.file_edit_tool.handler import handle_edit
from tools.implementations.file_edit_tool.schema import FileEditInput
from tools.implementations.file_read_tool.handler import format_model_content, handle_read
from tools.implementations.file_read_tool.schema import FileReadInput
from tools.implementations.file_write_tool.handler import handle_write
from tools.implementations.file_write_tool.schema import FileWriteInput
from tools.implementations.runtime.errors import ToolExecutionError


@pytest.mark.asyncio
async def test_read_returns_baseline_in_model_text(workspace):
    (workspace / "a.py").write_text("a\nb\nc\n", encoding="utf-8")
    structured = await handle_read(FileReadInput(file_path="a.py"), None)
    assert structured["mtime"] == int(os.stat(workspace / "a.py").st_mtime)
    assert structured["size"] > 0
    text = format_model_content(structured)
    assert f"mtime={structured['mtime']}" in text
    assert f"size={structured['size']}" in text


@pytest.mark.asyncio
async def test_read_segmented(workspace):
    (workspace / "a.py").write_text("\n".join(str(i) for i in range(1, 101)), encoding="utf-8")
    structured = await handle_read(FileReadInput(file_path="a.py", offset=50, limit=10), None)
    assert structured["start_line"] == 50
    assert structured["end_line"] == 59


@pytest.mark.asyncio
async def test_read_default_cap_hint(workspace):
    (workspace / "a.py").write_text("\n".join(str(i) for i in range(1, 5000)), encoding="utf-8")
    structured = await handle_read(FileReadInput(file_path="a.py"), None)
    assert structured["total_lines"] == 4999
    # 默认只读前 2000 行，带分段提示
    assert "offset/limit" in structured["content"]


@pytest.mark.asyncio
async def test_write_overwrite_requires_baseline(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    with pytest.raises(ToolExecutionError) as exc:
        await handle_write(FileWriteInput(file_path="a.py", content="new"), None)
    assert exc.value.code == "missing_baseline"


@pytest.mark.asyncio
async def test_write_overwrite_conflict(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    with pytest.raises(ToolExecutionError) as exc:
        await handle_write(
            FileWriteInput(file_path="a.py", content="new", base_mtime=0, base_size=0), None
        )
    assert exc.value.code == "file_modified"
    assert (workspace / "a.py").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_write_overwrite_success_with_baseline(workspace):
    (workspace / "a.py").write_text("old", encoding="utf-8")
    st = os.stat(workspace / "a.py")
    result = await handle_write(
        FileWriteInput(file_path="a.py", content="new", base_mtime=int(st.st_mtime), base_size=st.st_size),
        None,
    )
    assert result["action"] == "overwritten"
    assert (workspace / "a.py").read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_create_new_file(workspace):
    result = await handle_write(FileWriteInput(file_path="new.py", content="x = 1"), None)
    assert result["action"] == "created"
    assert (workspace / "new.py").read_text(encoding="utf-8") == "x = 1"


@pytest.mark.asyncio
async def test_edit_writeback_conflict(workspace):
    (workspace / "a.py").write_text("hello world\n", encoding="utf-8")
    with pytest.raises(ToolExecutionError) as exc:
        await handle_edit(
            FileEditInput(file_path="a.py", old_string="hello", new_string="hi", base_mtime=0, base_size=0),
            None,
        )
    assert exc.value.code == "file_modified"


@pytest.mark.asyncio
async def test_edit_normal_success(workspace):
    (workspace / "a.py").write_text("hello world\n", encoding="utf-8")
    result = await handle_edit(
        FileEditInput(file_path="a.py", old_string="hello", new_string="hi"), None
    )
    assert result["replacements"] == 1
    assert (workspace / "a.py").read_text(encoding="utf-8") == "hi world\n"
