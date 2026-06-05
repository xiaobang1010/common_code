"""BashTool — 执行 shell 命令。"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------

class BashInput(BaseModel):
    """Bash 工具输入。"""

    command: str
    timeout: int | None = 120000
    description: str | None = None


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------

BASH_PROMPT = """\
执行 shell 命令并返回输出。

使用说明：
- 工作目录在命令之间保持不变，但 shell 状态不会持久化
- 始终用双引号包裹包含空格的文件路径
- 尽量在会话中通过使用绝对路径来维持当前工作目录
- 可以指定可选的超时时间（毫秒），默认 120000ms（2 分钟）
- 发出多个命令时：
  - 如果命令相互独立且可以并行运行，在一条消息中发起多个 Bash 工具调用
  - 如果命令相互依赖且必须按顺序运行，使用 '&&' 将它们串联
"""


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------

def _validate_input(inp: BashInput, _context: ToolUseContext) -> dict[str, Any]:
    """检查命令是否为空。"""
    if not inp.command.strip():
        return {"result": False, "message": "命令不能为空"}
    return {"result": True}


# ---------------------------------------------------------------------------
# 获取 shell 命令
# ---------------------------------------------------------------------------

def _get_shell_cmd() -> list[str]:
    """获取当前平台的 shell 命令。

    Windows: 使用 PowerShell 7+ (pwsh) 或 Windows PowerShell
    其他平台: 使用 /bin/sh
    """
    if sys.platform == "win32":
        # 优先使用 pwsh (PowerShell 7+)，fallback 到 powershell.exe
        for exe in ("pwsh", "powershell"):
            if os.environ.get("PATH"):
                for path in os.environ["PATH"].split(os.pathsep):
                    full = os.path.join(path, f"{exe}.exe")
                    if os.path.isfile(full):
                        return [full, "-NoProfile", "-Command"]
        # 最终 fallback
        return ["powershell", "-NoProfile", "-Command"]
    return ["/bin/sh", "-c"]


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def _execute(inp: BashInput, _context: ToolUseContext) -> ToolResult:
    """使用 asyncio.create_subprocess_exec 执行命令。"""
    timeout_sec = (inp.timeout or 120000) / 1000.0
    shell_cmd = _get_shell_cmd()

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            inp.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult(
            content=f"命令超时（{inp.timeout}ms）",
            is_error=True,
        )
    except Exception as exc:
        return ToolResult(
            content=f"命令执行失败：{exc}",
            is_error=True,
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    output_parts: list[str] = []
    if stdout.strip():
        output_parts.append(stdout.strip())
    if stderr.strip():
        output_parts.append(stderr.strip())

    output = "\n".join(output_parts)
    is_error = proc.returncode != 0

    if is_error and proc.returncode is not None:
        output += f"\nExit code {proc.returncode}"

    return ToolResult(content=output, is_error=is_error)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_bash_tool() -> Tool:
    """返回 BashTool 实例。"""
    return build_tool(
        name="Bash",
        description="执行 shell 命令",
        input_schema=BashInput,
        execute=_execute,
        prompt=BASH_PROMPT,
        validate_input=_validate_input,
    )


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    async def _test():
        tool = get_bash_tool()
        # 测试 1：echo hello
        inp = BashInput(command="echo hello")
        result = await tool.execute(inp, ToolUseContext())
        assert "hello" in result.content, f"期望包含 'hello'，实际：{result.content}"
        assert not result.is_error, "期望 is_error=False"
        print("[PASS] echo hello")

        # 测试 2：空命令验证
        inp2 = BashInput(command="")
        validation = tool.validate_input(inp2, ToolUseContext())
        assert validation["result"] is False, "期望空命令验证失败"
        print("[PASS] 空命令验证")

        # 测试 3：错误退出码
        inp3 = BashInput(command="exit 1")
        result3 = await tool.execute(inp3, ToolUseContext())
        assert result3.is_error, "期望 is_error=True"
        print("[PASS] 错误退出码")

        print("\nAll BashTool tests passed!")

    asyncio.run(_test())
