"""Bash 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

import asyncio
import os
import sys

from tools.implementations.bash_tool.schema import BashInput
from tools.implementations.runtime.errors import ToolExecutionError
from tools.protocol import TimeoutPolicy, ToolUseContext


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
        return ["powershell", "-NoProfile", "-Command"]
    return ["/bin/sh", "-c"]


def validate_bash_input(inp: BashInput) -> None:
    """输入前置校验：命令不能为空。

    Raises:
        ToolExecutionError: 命令为空
    """
    if not inp.command.strip():
        raise ToolExecutionError("empty_command", "命令不能为空")


async def handle_bash(
    inp: BashInput,
    context: ToolUseContext,
    timeout_policy: TimeoutPolicy,
) -> dict:
    """使用 asyncio.create_subprocess_exec 执行命令。

    超时由描述符策略解析（输入可覆盖但被钳制到上限）；
    任务被取消时终止子进程后向上抛出 CancelledError。

    Returns:
        结构化结果字典：
        {
            "stdout": 标准输出, "stderr": 标准错误,
            "exit_code": 退出码, "timed_out": 是否超时,
            "effective_timeout_ms": 实际生效的超时,
        }

    Raises:
        ToolExecutionError: 命令为空 / 进程创建失败
    """
    validate_bash_input(inp)

    # 超时策略：允许调用覆盖，但钳制到描述符上限
    timeout_ms = timeout_policy.resolve_ms(inp.timeout)
    timeout_sec = timeout_ms / 1000.0
    shell_cmd = _get_shell_cmd()

    # Windows PowerShell 默认输出 ANSI 颜色码，前端不解析会导致乱码
    if sys.platform == "win32":
        actual_command = f"$PSStyle.OutputRendering='PlainText'; {inp.command}"
    else:
        actual_command = inp.command

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            actual_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        raise ToolExecutionError("spawn_failed", f"命令启动失败：{exc}")

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
        timed_out = False
    except asyncio.TimeoutError:
        # 超时：终止子进程，返回超时结果（不作为异常）
        proc.kill()
        await proc.wait()
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "timed_out": True,
            "effective_timeout_ms": timeout_ms,
        }
    except asyncio.CancelledError:
        # 取消：终止子进程后向上抛出，由执行管线处理
        proc.kill()
        await proc.wait()
        raise

    return {
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "exit_code": proc.returncode,
        "timed_out": False,
        "effective_timeout_ms": timeout_ms,
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本（不做截断，由结果预算统一治理）。"""
    if structured.get("timed_out"):
        return f"命令超时（{structured.get('effective_timeout_ms')}ms）"

    stdout = (structured.get("stdout") or "").strip()
    stderr = (structured.get("stderr") or "").strip()
    exit_code = structured.get("exit_code")

    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    output = "\n".join(parts)

    if exit_code not in (0, None):
        output += f"\nExit code {exit_code}"
    return output
