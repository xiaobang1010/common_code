"""会话级状态搭建，参考原始 setup.ts 的设计。

在交互式/非交互式会话启动时执行，完成以下步骤：
  1. setCwd — 设置工作目录到 bootstrap state
  2. find_git_root — 查找 git 根目录
  3. capture_hooks_config_snapshot — 捕获 hooks 快照
  4. 初始化权限模式
  5. 设置初始 AppState
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from startup.bootstrap.state import (
    get_cwd_state,
    get_model,
    get_session_id,
    set_cwd_state,
    set_original_cwd,
    set_permission_mode,
    set_project_root,
)
from startup.state.app_state import AppState, AppStateProvider
from startup.utils.hooks import capture_hooks_config_snapshot, HookConfig

logger = logging.getLogger(__name__)

# 模块级 hooks 快照缓存
_hooks_snapshot: HookConfig | None = None


# ---------------------------------------------------------------------------
# set_cwd — 设置工作目录
# ---------------------------------------------------------------------------


def set_cwd(cwd: str) -> None:
    """设置工作目录到 bootstrap state。

    同时更新 os.getcwd() 和 bootstrap state 中的 cwd/original_cwd/project_root。

    Args:
        cwd: 目标工作目录路径
    """
    normalized = os.path.normpath(cwd)
    set_cwd_state(normalized)
    set_original_cwd(normalized)
    set_project_root(normalized)
    try:
        os.chdir(normalized)
    except OSError as e:
        logger.warning("chdir 失败: %s — %s", normalized, e)


# ---------------------------------------------------------------------------
# find_git_root — 查找 git 根目录
# ---------------------------------------------------------------------------


def find_git_root(path: str) -> str | None:
    """从 path 向上查找 .git 目录，返回 git 仓库根路径。

    Args:
        path: 起始查找路径

    Returns:
        git 根目录路径，如果未找到返回 None
    """
    current = Path(path).resolve()
    while True:
        git_dir = current / ".git"
        if git_dir.exists():
            return str(current)
        parent = current.parent
        if parent == current:
            # 已到文件系统根
            return None
        current = parent


# ---------------------------------------------------------------------------
# setup — 会话级状态搭建
# ---------------------------------------------------------------------------


async def setup(
    cwd: str | None = None,
    permission_mode: str = "default",
    **kwargs: Any,
) -> AppStateProvider:
    """会话级状态搭建。

    执行顺序：
      1. setCwd(cwd or os.getcwd()) — 设置工作目录
      2. find_git_root() — 查找 git 根目录
      3. capture_hooks_config_snapshot() — 捕获 hooks 快照
      4. 初始化权限模式
      5. 设置初始 AppState

    Args:
        cwd: 工作目录，默认为 os.getcwd()
        permission_mode: 权限模式，默认 "default"
        **kwargs: 额外参数（预留扩展）

    Returns:
        初始化后的 AppStateProvider 实例
    """
    global _hooks_snapshot

    # 1. 设置工作目录
    resolved_cwd = cwd or os.getcwd()
    set_cwd(resolved_cwd)
    logger.info("工作目录设置为: %s", resolved_cwd)

    # 2. 查找 git 根目录
    git_root = find_git_root(resolved_cwd)
    if git_root:
        logger.info("Git 根目录: %s", git_root)
    else:
        logger.info("未找到 Git 仓库")

    # 3. 捕获 hooks 配置快照
    #    IMPORTANT: 必须在 setCwd() 之后调用，确保 hooks 从正确目录加载
    _hooks_snapshot = capture_hooks_config_snapshot()
    logger.info(
        "Hooks 快照已捕获: pre=%d, post=%d",
        len(_hooks_snapshot.pre_tool_use),
        len(_hooks_snapshot.post_tool_use),
    )

    # 4. 初始化权限模式
    set_permission_mode(permission_mode)
    logger.info("权限模式: %s", permission_mode)

    # 5. 设置初始 AppState
    app_state = AppState(
        session_id=get_session_id(),
        model=get_model(),
        permission_mode=permission_mode,
    )
    provider = AppStateProvider(app_state)

    return provider


# ---------------------------------------------------------------------------
# Hooks 快照访问
# ---------------------------------------------------------------------------


def get_hooks_snapshot() -> HookConfig | None:
    """获取当前 hooks 配置快照。"""
    return _hooks_snapshot


def update_hooks_snapshot() -> None:
    """重新捕获 hooks 配置快照（工作目录变更后调用）。"""
    global _hooks_snapshot
    _hooks_snapshot = capture_hooks_config_snapshot()


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import sys
    import tempfile

    # 确保项目根目录在 sys.path 上（支持 from state.xxx 等非限定导入）
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    print("=" * 60)
    print("setup.py 测试")
    print("=" * 60)

    # 测试 1: set_cwd 设置工作目录
    print("\n--- 测试 1: set_cwd 设置工作目录 ---")
    try:
        original = os.getcwd()
        set_cwd(original)
        assert get_cwd_state() == os.path.normpath(original), f"cwd 不匹配: {get_cwd_state()} vs {os.path.normpath(original)}"
        print(f"  cwd = {get_cwd_state()}")
        print("  [PASS] set_cwd 设置工作目录")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 2: set_cwd 使用临时目录
    print("\n--- 测试 2: set_cwd 使用临时目录 ---")
    try:
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            set_cwd(tmpdir)
            assert os.getcwd() == os.path.normpath(tmpdir), f"os.getcwd() 不匹配"
            assert get_cwd_state() == os.path.normpath(tmpdir), f"state cwd 不匹配"
        # 恢复
        set_cwd(original)
        print(f"  临时目录设置成功，已恢复到 {os.getcwd()}")
        print("  [PASS] set_cwd 使用临时目录")
    except Exception as e:
        print(f"  [FAIL] {e}")
        # 确保恢复工作目录
        try:
            os.chdir(original)
        except OSError:
            pass

    # 测试 3: find_git_root 在 git 仓库中
    print("\n--- 测试 3: find_git_root 在 git 仓库中 ---")
    try:
        cwd = os.getcwd()
        git_root = find_git_root(cwd)
        if git_root:
            print(f"  当前目录 git 根: {git_root}")
            assert (Path(git_root) / ".git").exists(), "返回的路径应包含 .git"
            print("  [PASS] find_git_root 在 git 仓库中")
        else:
            print("  当前目录不在 git 仓库中，跳过此测试")
            print("  [SKIP] find_git_root 在 git 仓库中")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 4: find_git_root 在非 git 目录中
    print("\n--- 测试 4: find_git_root 在非 git 目录中 ---")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_git_root(tmpdir)
            # 临时目录可能不在 git 仓库中，也可能在（如果系统临时目录在 git 下）
            print(f"  临时目录 find_git_root: {result}")
            print("  [PASS] find_git_root 在非 git 目录中（无异常）")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 5: find_git_root 边界情况
    print("\n--- 测试 5: find_git_root 边界情况 ---")
    try:
        # 不存在的路径 — resolve 后向上查找
        result = find_git_root("/nonexistent/path/to/dir")
        print(f"  不存在路径: {result}")
        print("  [PASS] find_git_root 边界情况（无异常）")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 6: setup() 完整流程
    print("\n--- 测试 6: setup() 完整流程 ---")
    try:
        from startup.bootstrap.state import reset_state_for_tests
        reset_state_for_tests()

        provider = asyncio.run(setup(cwd=os.getcwd(), permission_mode="default"))
        state = provider.get_state()

        assert state.session_id is not None, "session_id 应已设置"
        assert state.permission_mode == "default", f"permission_mode 应为 default, got {state.permission_mode}"
        assert get_cwd_state() == os.path.normpath(os.getcwd()), "cwd 应已设置"

        snapshot = get_hooks_snapshot()
        assert snapshot is not None, "hooks 快照应已捕获"

        print(f"  session_id = {state.session_id}")
        print(f"  permission_mode = {state.permission_mode}")
        print(f"  cwd = {get_cwd_state()}")
        print(f"  hooks_snapshot: pre={len(snapshot.pre_tool_use)}, post={len(snapshot.post_tool_use)}")
        print("  [PASS] setup() 完整流程")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 测试 7: update_hooks_snapshot()
    print("\n--- 测试 7: update_hooks_snapshot() ---")
    try:
        update_hooks_snapshot()
        snapshot = get_hooks_snapshot()
        assert snapshot is not None, "更新后快照应存在"
        print(f"  更新后 hooks_snapshot: pre={len(snapshot.pre_tool_use)}, post={len(snapshot.post_tool_use)}")
        print("  [PASS] update_hooks_snapshot()")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
