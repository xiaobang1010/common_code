"""上下文构建模块。

参考原始 TypeScript 实现 src/context.ts，提供系统和用户上下文构建。

系统上下文（get_system_context）：
  - git status（当前分支、是否有未提交更改）
  - cache breaker（时间戳，防止缓存命中）

用户上下文（get_user_context）：
  - AGENT.md 文件内容（项目级指令）
  - 当前日期时间
  - 工作目录信息

使用 functools.lru_cache 实现 memoize。
"""

from __future__ import annotations

import functools
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _run_git(*args: str, cwd: str | None = None) -> str:
    """执行 git 命令并返回 stdout，失败返回空字符串。"""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _is_git_repo(cwd: str | None = None) -> bool:
    """检查当前目录是否在 git 仓库中。"""
    return _run_git("rev-parse", "--is-inside-work-tree", cwd=cwd) == "true"


def _get_git_root(cwd: str | None = None) -> str | None:
    """获取 git 仓库根目录。"""
    root = _run_git("rev-parse", "--show-toplevel", cwd=cwd)
    return root if root else None


# ---------------------------------------------------------------------------
# AGENT.md 查找
# ---------------------------------------------------------------------------


def find_agent_md(cwd: str | None = None) -> str | None:
    """查找 AGENT.md 文件。

    从当前目录向上查找到 git root（或文件系统根），
    返回找到的第一个 AGENT.md 的完整路径。
    """
    start = Path(cwd or os.getcwd()).resolve()

    # 确定搜索上界
    git_root = _get_git_root(cwd)
    if git_root:
        upper_bound = Path(git_root).resolve()
    else:
        upper_bound = start.root

    current = start
    while True:
        agent_md = current / "AGENT.md"
        if agent_md.is_file():
            return str(agent_md)

        # 到达上界
        if current == upper_bound:
            break

        parent = current.parent
        if parent == current:
            # 到达文件系统根
            break
        current = parent

    return None


# ---------------------------------------------------------------------------
# 系统上下文
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_system_context() -> str:
    """获取系统上下文（memoized）。

    包含：
      - git status（当前分支、是否有未提交更改）
      - cache breaker（时间戳，防止缓存命中）
    """
    parts: list[str] = []

    # Git status
    cwd = os.getcwd()
    if _is_git_repo(cwd):
        branch = _run_git("branch", "--show-current", cwd=cwd)
        status = _run_git("status", "--short", cwd=cwd)

        git_info_parts = [
            "This is the git status at the start of the conversation. "
            "Note that this status is a snapshot in time, and will not update during the conversation.",
        ]
        if branch:
            git_info_parts.append(f"Current branch: {branch}")
        if status:
            # 截断过长的 status
            if len(status) > 2000:
                status = status[:2000] + (
                    "\n... (truncated because it exceeds 2k characters. "
                    'If you need more information, run "git status" using BashTool)'
                )
            git_info_parts.append(f"Status:\n{status}")
        else:
            git_info_parts.append("Status:\n(clean)")

        parts.append("\n\n".join(git_info_parts))

    # Cache breaker
    cache_breaker = f"[CACHE_BREAKER: {int(time.time())}]"
    parts.append(cache_breaker)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 用户上下文
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_user_context() -> str:
    """获取用户上下文（memoized）。

    包含：
      - AGENT.md 文件内容（项目级指令）
      - 当前日期时间
      - 工作目录信息
    """
    parts: list[str] = []

    # AGENT.md 内容
    agent_md_path = find_agent_md()
    if agent_md_path:
        try:
            content = Path(agent_md_path).read_text(encoding="utf-8")
            if content.strip():
                parts.append(content.strip())
        except OSError:
            pass

    # 当前日期时间
    now = datetime.now(timezone.utc)
    local_date = now.strftime("%Y-%m-%d")
    parts.append(f"Today's date is {local_date}.")

    # 工作目录
    cwd = os.getcwd()
    parts.append(f"Current working directory: {cwd}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 缓存清理
# ---------------------------------------------------------------------------


def clear_context_cache() -> None:
    """清除上下文缓存。

    当系统提示注入变更时调用，确保下次获取上下文时重新计算。
    """
    get_system_context.cache_clear()
    get_user_context.cache_clear()


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("上下文构建测试")
    print("=" * 60)

    # 测试 1: get_system_context
    print("\n--- 测试 1: get_system_context ---")
    ctx = get_system_context()
    assert isinstance(ctx, str), "返回值应为字符串"
    assert len(ctx) > 0, "系统上下文不应为空"
    assert "CACHE_BREAKER" in ctx, "应包含 cache breaker"
    print(f"  系统上下文长度: {len(ctx)}")
    print(f"  包含 CACHE_BREAKER: {'CACHE_BREAKER' in ctx}")
    # 检查是否在 git 仓库中
    if _is_git_repo():
        assert "Current branch" in ctx or "Status" in ctx, "git 仓库应包含分支信息"
        print("  包含 git 信息: True")
    else:
        print("  不在 git 仓库中，跳过 git 信息检查")
    print("  [PASS] get_system_context")

    # 测试 2: get_user_context
    print("\n--- 测试 2: get_user_context ---")
    uctx = get_user_context()
    assert isinstance(uctx, str), "返回值应为字符串"
    assert len(uctx) > 0, "用户上下文不应为空"
    assert "Today's date" in uctx, "应包含日期信息"
    assert "Current working directory" in uctx, "应包含工作目录"
    print(f"  用户上下文长度: {len(uctx)}")
    print(f"  包含日期: {'Today' in uctx}")
    print(f"  包含工作目录: {'Current working directory' in uctx}")
    print("  [PASS] get_user_context")

    # 测试 3: find_agent_md
    print("\n--- 测试 3: find_agent_md ---")
    agent_md = find_agent_md()
    if agent_md:
        print(f"  找到 AGENT.md: {agent_md}")
    else:
        print("  未找到 AGENT.md（正常，取决于项目结构）")
    print("  [PASS] find_agent_md")

    # 测试 4: memoize
    print("\n--- 测试 4: memoize ---")
    ctx1 = get_system_context()
    ctx2 = get_system_context()
    assert ctx1 is ctx2, "多次调用应返回同一对象（memoized）"
    print("  get_system_context memoize: OK")

    uctx1 = get_user_context()
    uctx2 = get_user_context()
    assert uctx1 is uctx2, "多次调用应返回同一对象（memoized）"
    print("  get_user_context memoize: OK")
    print("  [PASS] memoize")

    # 测试 5: clear_context_cache
    print("\n--- 测试 5: clear_context_cache ---")
    old_ctx = get_system_context()
    clear_context_cache()
    new_ctx = get_system_context()
    # cache breaker 时间戳可能不同
    print("  缓存清除后重新获取: OK")
    print("  [PASS] clear_context_cache")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
