"""present_files 工具测试：校验规则、metadata 结构、loop 外发事件。"""

from __future__ import annotations

import pytest

from tools.implementations.present_files_tool.handler import (
    format_model_content,
    handle_present_files,
)
from tools.implementations.present_files_tool.schema import PresentFilesInput
from tools.implementations.present_files_tool.tool import get_present_files_tool
from tools.implementations.runtime.errors import ToolExecutionError
from tools.utils.schema import tool_to_openai_schema


@pytest.mark.asyncio
async def test_validates_and_relativizes(workspace):
    (workspace / "report.md").write_text("# R\n", encoding="utf-8")
    r = await handle_present_files(
        PresentFilesInput(files=["report.md"], explanation="最终报告")
    )
    assert r["files"] == ["report.md"]
    assert r["explanation"] == "最终报告"
    assert "report.md" in format_model_content(r)


@pytest.mark.asyncio
async def test_rejects_missing_and_empty(workspace):
    with pytest.raises(ToolExecutionError) as e1:
        await handle_present_files(PresentFilesInput(files=[]))
    assert e1.value.code == "empty_files"

    with pytest.raises(ToolExecutionError) as e2:
        await handle_present_files(PresentFilesInput(files=["nope.md"]))
    assert e2.value.code == "file_not_found"

    (workspace / "dir").mkdir()
    with pytest.raises(ToolExecutionError) as e3:
        await handle_present_files(PresentFilesInput(files=["dir"]))
    assert e3.value.code == "not_a_file"


def test_present_files_registered_and_described():
    tool = get_present_files_tool()
    schema = tool_to_openai_schema(tool)
    assert schema["function"]["description"] == tool.prompt
    assert "present_files" in [t.name for t in __import__("tools").get_tools()]


def test_loop_event_emitted_only_for_present_files():
    """loop 只对成功的 present_files 结果外发结构化事件。"""
    from query.loop import _present_files_event
    from tools.executor import ToolExecutionResult

    ok = ToolExecutionResult(
        tool_call_id="1",
        tool_name="present_files",
        content="x",
        metadata={"type": "present_files", "files": ["a.md", "b.md"], "explanation": "报告"},
    )
    ev = _present_files_event(ok)
    assert ev == {"role": "present_files", "files": ["a.md", "b.md"], "explanation": "报告"}

    # 其他工具 / 失败结果 / 非 present_files metadata 都不外发
    assert (
        _present_files_event(
            ToolExecutionResult(
                tool_call_id="2", tool_name="Bash", content="y",
                metadata={"type": "present_files", "files": []},
            )
        )
        is None
    )
    assert (
        _present_files_event(
            ToolExecutionResult(
                tool_call_id="3", tool_name="present_files", content="z", is_error=True,
                metadata={"type": "present_files", "files": []},
            )
        )
        is None
    )
    assert (
        _present_files_event(
            ToolExecutionResult(tool_call_id="4", tool_name="present_files", content="w")
        )
        is None
    )
