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


@pytest.mark.asyncio
async def test_read_kernel_runs_off_loop(workspace, monkeypatch):
    """同步读内核被丢线程池：内核慢 IO 期间事件循环保持响应。

    用 sleep 模拟慢磁盘，探针协程期望 20ms 一拍；
    若内核跑在事件循环上，探针会整体冻结 0.3s。
    """
    import asyncio
    import time

    import tools.implementations.file_read_tool.handler as read_handler

    def slow_kernel(inp):
        time.sleep(0.3)  # 模拟慢磁盘读
        return {"file_path": "fake", "content": "", "mtime": 0, "size": 0}

    monkeypatch.setattr(read_handler, "_read_sync", slow_kernel)

    probe_delays: list[float] = []
    stop = {"flag": False}

    async def probe() -> None:
        while not stop["flag"]:
            t0 = time.monotonic()
            await asyncio.sleep(0.02)
            probe_delays.append(time.monotonic() - t0 - 0.02)

    probe_task = asyncio.create_task(probe())
    await asyncio.sleep(0.05)

    structured = await read_handler.handle_read(
        read_handler.FileReadInput(file_path="a.py"), None
    )

    stop["flag"] = True
    await probe_task

    assert structured["file_path"] == "fake"
    assert max(probe_delays) < 0.1, (
        f"探针最大延迟 {max(probe_delays):.3f}s，"
        "疑似同步读内核跑在了事件循环上"
    )


@pytest.mark.asyncio
async def test_write_atomic_failure_keeps_original(workspace, monkeypatch):
    """原子写失败路径：替换失败时清理临时文件、原文件内容不变。"""
    import os as _os

    (workspace / "a.txt").write_text("原内容", encoding="utf-8")
    st = os.stat(workspace / "a.txt")

    import tools.implementations.file_write_tool.handler as write_handler

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(write_handler.os, "replace", boom)

    with pytest.raises(OSError):
        await write_handler.handle_write(
            FileWriteInput(
                file_path="a.txt",
                content="新内容",
                base_mtime=int(st.st_mtime),
                base_size=st.st_size,
            ),
            None,
        )

    # 原文件未被破坏
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "原内容"
    # 无残留临时文件
    leftovers = [p.name for p in workspace.iterdir() if p.name.startswith(".cc-write-")]
    assert leftovers == []


# --- 自动基线：Read/Write/Edit 登记后覆盖写免手工回传 ---


@pytest.mark.asyncio
async def test_write_overwrite_after_read_uses_recorded_baseline(workspace):
    """Read 后覆盖写无需回传基线：系统自动采用登记值。"""
    (workspace / "a.py").write_text("old", encoding="utf-8")
    await handle_read(FileReadInput(file_path="a.py"), None)
    result = await handle_write(FileWriteInput(file_path="a.py", content="new"), None)
    assert result["action"] == "overwritten"
    assert (workspace / "a.py").read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_overwrite_after_previous_write_succeeds(workspace):
    """连续覆盖写：上次写盘成功后登记新基线，再次覆盖无需重新 Read。"""
    await handle_write(FileWriteInput(file_path="a.py", content="v1"), None)
    result = await handle_write(FileWriteInput(file_path="a.py", content="v2"), None)
    assert result["action"] == "overwritten"
    assert (workspace / "a.py").read_text(encoding="utf-8") == "v2"


@pytest.mark.asyncio
async def test_write_overwrite_after_edit_uses_recorded_baseline(workspace):
    """Edit 写盘后同样登记基线，后续 Write 覆盖免 Read。"""
    (workspace / "a.py").write_text("hello world\n", encoding="utf-8")
    await handle_edit(FileEditInput(file_path="a.py", old_string="hello", new_string="hi"), None)
    result = await handle_write(FileWriteInput(file_path="a.py", content="full rewrite"), None)
    assert result["action"] == "overwritten"
    assert (workspace / "a.py").read_text(encoding="utf-8") == "full rewrite"


@pytest.mark.asyncio
async def test_write_overwrite_stale_recorded_baseline_conflict(workspace):
    """登记基线后磁盘被外部改动：自动基线照样拦截（file_modified）。"""
    (workspace / "a.py").write_text("old", encoding="utf-8")
    await handle_read(FileReadInput(file_path="a.py"), None)
    (workspace / "a.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(ToolExecutionError) as exc:
        await handle_write(FileWriteInput(file_path="a.py", content="new"), None)
    assert exc.value.code == "file_modified"
    assert (workspace / "a.py").read_text(encoding="utf-8") == "tampered"


@pytest.mark.asyncio
async def test_write_overwrite_requires_baseline_message_points_to_read(workspace):
    """从未读取过的文件覆盖被拒时，文案点名 Read 工具与免手工传参。"""
    (workspace / "a.py").write_text("old", encoding="utf-8")
    with pytest.raises(ToolExecutionError) as exc:
        await handle_write(FileWriteInput(file_path="a.py", content="new"), None)
    assert exc.value.code == "missing_baseline"
    assert "Read" in exc.value.message
    assert "无需手动传参" in exc.value.message
