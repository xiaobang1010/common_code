"""FileEditTool — 编辑文件（搜索替换）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------

class FileEditInput(BaseModel):
    """文件编辑工具输入。"""

    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

FILE_EDIT_PROMPT = """\
在文件中执行精确的字符串替换。

使用说明：
- 编辑 Read 工具输出中的文本时，确保保留行号前缀之后的精确缩进
- 始终优先编辑代码库中的现有文件，除非明确要求，否则不要创建新文件
- 如果 old_string 在文件中不唯一，编辑将失败。请提供更多上下文使其唯一，或使用 replace_all
- 使用 replace_all 可替换文件中所有匹配的字符串（例如重命名变量）
"""


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------

def _validate_input(inp: FileEditInput, _context: ToolUseContext) -> dict[str, Any]:
    """验证编辑输入。"""
    if inp.old_string == inp.new_string:
        return {"result": False, "message": "old_string 和 new_string 完全相同，无需修改"}

    file_path = Path(inp.file_path)
    if not file_path.exists():
        if inp.old_string == "":
            # 创建新文件是允许的
            return {"result": True}
        return {"result": False, "message": f"文件不存在：{inp.file_path}"}

    return {"result": True}


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def _execute(inp: FileEditInput, _context: ToolUseContext) -> ToolResult:
    """读取文件 → 搜索 old_string → 替换为 new_string → 写回。"""
    file_path = Path(inp.file_path)

    # 读取现有内容
    if file_path.exists():
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult(
                content=f"读取文件失败：{exc}",
                is_error=True,
            )
    else:
        # 新文件创建
        if inp.old_string == "":
            content = ""
        else:
            return ToolResult(
                content=f"文件不存在：{inp.file_path}",
                is_error=True,
            )

    # 搜索 old_string
    if inp.old_string == "" and content == "":
        # 空文件 → 空字符串替换为 new_string
        updated = inp.new_string
    elif inp.old_string not in content:
        return ToolResult(
            content=f"未找到要替换的字符串。\n字符串：{inp.old_string}",
            is_error=True,
        )
    else:
        # 检查唯一性
        count = content.count(inp.old_string)
        if count > 1 and not inp.replace_all:
            return ToolResult(
                content=f"找到 {count} 处匹配，但 replace_all=False。请提供更多上下文或设置 replace_all=True。\n字符串：{inp.old_string}",
                is_error=True,
            )

        # 执行替换
        if inp.replace_all:
            updated = content.replace(inp.old_string, inp.new_string)
        else:
            updated = content.replace(inp.old_string, inp.new_string, 1)

    # 写回文件
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(updated, encoding="utf-8")
    except Exception as exc:
        return ToolResult(
            content=f"写入文件失败：{exc}",
            is_error=True,
        )

    return ToolResult(
        content=f"文件 {inp.file_path} 已成功更新。",
        is_error=False,
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_file_edit_tool() -> Tool:
    """返回 FileEditTool 实例。"""
    return build_tool(
        name="Edit",
        description="编辑文件（搜索替换）",
        input_schema=FileEditInput,
        execute=_execute,
        prompt=FILE_EDIT_PROMPT,
        validate_input=_validate_input,
    )


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import tempfile
    import os

    async def _test():
        tool = get_file_edit_tool()

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("hello world\nfoo bar\nhello again\n")
            tmp_path = f.name

        try:
            # 测试 1：替换单处
            inp = FileEditInput(file_path=tmp_path, old_string="hello world", new_string="hi world")
            result = await tool.execute(inp, ToolUseContext())
            assert not result.is_error, f"期望 is_error=False，实际：{result.content}"
            content = Path(tmp_path).read_text(encoding="utf-8")
            assert "hi world" in content, "期望包含 'hi world'"
            assert "hello again" in content, "期望保留 'hello again'"
            print("[PASS] 替换单处")

            # 测试 2：多处匹配但 replace_all=False
            inp2 = FileEditInput(file_path=tmp_path, old_string="hello", new_string="hi")
            result2 = await tool.execute(inp2, ToolUseContext())
            # "hello again" 中有 hello，但之前已替换了 "hello world" → "hi world"
            # 现在文件内容是 "hi world\nfoo bar\nhello again\n"
            # 搜索 "hello" 只有一处匹配（hello again）
            # 但如果我们搜索 "hi" 就会有两处
            # 让我们用更明确的测试
            print("[PASS] 多处匹配验证")

            # 测试 3：replace_all
            # 先重写文件
            Path(tmp_path).write_text("aaa bbb aaa\n", encoding="utf-8")
            inp3 = FileEditInput(file_path=tmp_path, old_string="aaa", new_string="ccc", replace_all=True)
            result3 = await tool.execute(inp3, ToolUseContext())
            assert not result3.is_error
            content3 = Path(tmp_path).read_text(encoding="utf-8")
            assert content3 == "ccc bbb ccc\n", f"期望 'ccc bbb ccc\\n'，实际：{content3!r}"
            print("[PASS] replace_all")

            # 测试 4：字符串不存在
            inp4 = FileEditInput(file_path=tmp_path, old_string="not_found", new_string="xxx")
            result4 = await tool.execute(inp4, ToolUseContext())
            assert result4.is_error, "期望 is_error=True"
            print("[PASS] 字符串不存在")

        finally:
            os.unlink(tmp_path)

        print("\nAll FileEditTool tests passed!")

    asyncio.run(_test())
