"""工作区路径沙箱 — 统一的路径解析与边界校验。

文件类工具（Read/Write/Edit/Glob/Grep）的所有路径输入都经
resolve_workspace_path 处理：
1. 相对路径基于工作区根目录解析
2. 绝对路径直接使用
3. normalize 后校验是否仍在工作区内，拦截 ../../ 目录穿越
"""

from __future__ import annotations

from pathlib import Path

from tools.implementations.runtime.errors import path_outside_workspace_error


def get_workspace_root() -> Path:
    """获取工作区根目录 - 统一读取 server.paths.effective_root()。

    后台任务上下文里优先取任务的 workspace_var（跨工作区任务隔离），
    否则回退全局 project_root()。不使用进程 cwd：工作区切换后
    进程 cwd 是启动时的静态值，两者会分叉。
    """
    from server.paths import effective_root

    return Path(effective_root())


def _allowed_roots(workspace_root: Path) -> list[Path]:
    """允许的根目录列表：工作区根 + additional_directories 白名单。

    配置的额外目录可扩大文件工具的访问范围（多源合并结果，
    见 startup.config.get_initial_settings）。
    """
    roots = [workspace_root]
    try:
        from startup.config import get_initial_settings

        additional = get_initial_settings(workspace_root).permissions.additional_directories
        for d in additional:
            p = Path(d).resolve()
            if p not in roots:
                roots.append(p)
    except Exception:
        pass  # 配置读取失败时仅工作区根
    return roots


def _is_within(path: Path, root: Path) -> bool:
    """判断 path 是否在 root 内（含 root 本身）。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_workspace_path(
    input_path: str,
    workspace_root: Path | None = None,
    *,
    must_exist: bool = False,
) -> Path:
    """解析并校验工具输入路径。

    Args:
        input_path: 工具输入的路径（相对或绝对）
        workspace_root: 工作区根目录，默认取当前工作目录
        must_exist: 是否要求路径必须存在（存在性检查交给调用方做更细的
            文件/目录区分，这里仅做可选的粗检查）

    Returns:
        解析后的绝对路径

    Raises:
        ToolExecutionError: 路径越出工作区边界（code=path_outside_workspace）
            或 must_exist=True 时路径不存在（code=file_not_found）
    """
    root = (workspace_root or get_workspace_root()).resolve()
    raw = Path(input_path)

    # 相对路径基于工作区根解析，绝对路径原样使用
    resolved = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()

    # 边界校验：normalize 后必须落在任一允许根内（拦截 ../../ 穿越）
    # 允许根 = 工作区根 + additional_directories 白名单
    if not any(_is_within(resolved, r) for r in _allowed_roots(root)):
        raise path_outside_workspace_error(input_path, str(root))

    if must_exist and not resolved.exists():
        from tools.implementations.runtime.errors import file_not_found_error
        raise file_not_found_error(input_path)

    return resolved
