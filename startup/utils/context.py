"""上下文构建模块。

参考原始 TypeScript 实现 src/context.ts，提供系统和用户上下文构建。

系统上下文（get_system_context）：
  - 返回 dict[str, str]，包含 gitStatus 字段
  - gitStatus 包含当前分支、主分支名、git 用户名、status、最近提交

用户上下文（get_user_context）：
  - 返回 dict[str, str]，包含 agentMd、cwd、currentDate 字段
  - AGENT.md 文件内容（项目级指令）
  - 当前日期（北京时间）
  - 工作目录

使用 functools.lru_cache 实现 memoize。
"""

from __future__ import annotations

import functools
import os
import subprocess
from datetime import datetime, timezone, timedelta
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


def _get_default_branch(cwd: str | None = None) -> str | None:
    """获取默认主分支名。

    先尝试通过 origin/HEAD 的 symbolic-ref 获取；
    若失败再依次判断 main、master 是否存在。
    """
    # 先尝试 origin/HEAD 的 symbolic-ref
    result = _run_git("symbolic-ref", "refs/remotes/origin/HEAD", "--short", cwd=cwd)
    if result:
        # 结果形如 origin/main，去掉前缀
        if result.startswith("origin/"):
            return result[len("origin/"):]
        return result

    # 依次尝试 main、master
    for candidate in ("main", "master"):
        if _run_git("rev-parse", "--verify", f"refs/heads/{candidate}", cwd=cwd):
            return candidate

    return None


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
def get_system_context() -> dict[str, str]:
    """获取系统上下文（memoized）。

    返回 dict[str, str]，包含 gitStatus 字段。
    不在 git 仓库时返回空字典。
    """
    cwd = os.getcwd()
    if not _is_git_repo(cwd):
        return {}

    branch = _run_git("branch", "--show-current", cwd=cwd)
    status = _run_git("status", "--short", cwd=cwd)
    main_branch = _get_default_branch(cwd)
    user_name = _run_git("config", "user.name", cwd=cwd)
    log = _run_git("log", "--oneline", "-n", "5", cwd=cwd)

    git_info_parts = [
        "This is the git status at the start of the conversation. "
        "Note that this status is a snapshot in time, and will not update during the conversation.",
    ]
    if branch:
        git_info_parts.append(f"Current branch: {branch}")
    if main_branch:
        git_info_parts.append(
            f"Main branch (you will usually use this for PRs): {main_branch}"
        )
    if user_name:
        git_info_parts.append(f"Git user: {user_name}")
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
    if log:
        git_info_parts.append(f"Recent commits:\n{log}")

    return {"gitStatus": "\n\n".join(git_info_parts)}


# ---------------------------------------------------------------------------
# 用户上下文
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _get_user_context_cached() -> dict[str, str]:
    """获取可缓存的用户上下文部分。

    包含 AGENT.md 内容和工作目录，这些在会话期间不会变化。
    日期不在此处缓存，由 get_user_context() 动态拼接。
    返回字典的一部分（agentMd、cwd）。
    """
    result: dict[str, str] = {}

    # AGENT.md 内容
    agent_md_path = find_agent_md()
    if agent_md_path:
        try:
            content = Path(agent_md_path).read_text(encoding="utf-8")
            if content.strip():
                result["agentMd"] = content.strip()
        except OSError:
            pass

    # 工作目录
    result["cwd"] = os.getcwd()

    return result


def get_user_context() -> dict[str, str]:
    """获取用户上下文。

    返回 dict[str, str]，包含：
      - agentMd：AGENT.md 文件内容（项目级指令，memoized）
      - cwd：当前工作目录（memoized）
      - currentDate：今日日期（北京时间，动态获取，不缓存）
    """
    # 缓存部分（agentMd、cwd），复制一份避免修改缓存对象
    result = dict(_get_user_context_cached())

    # 当前日期时间（北京时间，不缓存，每次动态获取）
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y年%m月%d日")
    result["currentDate"] = f"今日日期是 {date_str}"

    return result


# ---------------------------------------------------------------------------
# 缓存清理
# ---------------------------------------------------------------------------


def clear_context_cache() -> None:
    """清除上下文缓存。

    当系统提示注入变更时调用，确保下次获取上下文时重新计算。
    """
    get_system_context.cache_clear()
    _get_user_context_cached.cache_clear()


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
    assert isinstance(ctx, dict), "返回值应为字典"
    # 检查是否在 git 仓库中
    if _is_git_repo():
        assert "gitStatus" in ctx, "git 仓库的系统上下文应包含 gitStatus"
        git_status = ctx["gitStatus"]
        assert (
            "Current branch" in git_status or "Status" in git_status
        ), "gitStatus 应包含分支或状态信息"
        print("  包含 git 信息: True")
        print(f"  gitStatus 内容:\n{git_status}")
    else:
        print("  不在 git 仓库中，跳过 git 信息检查")
        assert ctx == {}, "非 git 仓库应返回空字典"
    print("  [PASS] get_system_context")

    # 测试 2: get_user_context
    print("\n--- 测试 2: get_user_context ---")
    uctx = get_user_context()
    assert isinstance(uctx, dict), "返回值应为字典"
    assert "currentDate" in uctx, "应包含 currentDate 字段"
    assert "Current working directory" not in uctx, "不应再包含拼字符串形式的工作目录"
    assert uctx["currentDate"].startswith("今日日期是"), "currentDate 应以 '今日日期是' 开头"
    assert "cwd" in uctx, "应包含 cwd 字段"
    print(f"  currentDate: {uctx['currentDate']}")
    print(f"  cwd: {uctx['cwd']}")
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
    # 注意：get_user_context 现在不含 lru_cache（日期动态），故不测其 memoize
    print("  [PASS] memoize")

    # 测试 5: clear_context_cache
    print("\n--- 测试 5: clear_context_cache ---")
    clear_context_cache()
    new_ctx = get_system_context()
    print("  缓存清除后重新获取: OK")
    print("  [PASS] clear_context_cache")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
