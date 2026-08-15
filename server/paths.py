"""工作区路径管理与路径安全检查。"""

from __future__ import annotations

import os


# 这些目录不展示给前端
EXCLUDED_DIRS = {"__pycache__", "node_modules", "dist", ".git"}

# 统一可编辑文件大小上限（字节）：人侧可编辑上限与 AI 写回上限共用同一套数字
MAX_EDITABLE_BYTES = 5 * 1024 * 1024

# 扩展名到语言标识的映射
EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".txt": "plaintext",
}

# 可变的项目根目录，支持工作区切换
_project_root_value: str = os.path.dirname(os.path.dirname(__file__))


def project_root() -> str:
    """返回当前工作区根目录。"""
    return _project_root_value


def effective_root() -> str:
    """返回当前执行上下文生效的工作区根目录。

    后台任务上下文优先取 workspace_var（任务启动时设置自己所属工作区，
    asyncio.Task 拷贝 context 保证任务内读取隔离），跨工作区后台任务的
    文件沙箱/Bash/记忆归属/提示词工作区信息都取它；
    非任务上下文（默认为空）回退全局 project_root()，行为不变。
    """
    try:
        from server import state

        task_workspace = state.workspace_var.get()
        if task_workspace:
            return task_workspace
    except Exception:
        pass
    return project_root()


def set_project_root(path: str) -> None:
    """切换工作区根目录。

    同步更新 startup.bootstrap.state 的 cwd（UserPromptSubmit hooks、
    engine 构造读取的就是它），保证切换后文件沙箱、Bash、hooks 与 UI
    指向同一工作区，避免三处根分叉。
    """
    global _project_root_value
    _project_root_value = path
    try:
        from startup.bootstrap.state import set_cwd_state

        set_cwd_state(path)
    except Exception:
        # 状态同步失败不阻断切换
        pass


def is_within_root(target: str, root: str) -> bool:
    """判断 target 是否仍在 root 目录内（含 root 本身）。"""
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        # 跨驱动器等情况，直接拒绝
        return False


def resolve_within_root(path: str) -> str:
    """解析相对路径并校验落在工作区根内，返回展开后的绝对路径。

    与 AI 工具的 resolve_workspace_path 对齐：先对目标路径做 realpath 展开
    （含软链接），再判断是否仍在工作区根内，同时拦截 ../ 目录穿越与指向
    工作区外的软链接穿越。用于 write/create 等写接口的安全校验。

    Raises:
        ValueError: 路径展开后落在工作区根之外
    """
    root = os.path.realpath(project_root())
    candidate = os.path.join(project_root(), path) if not os.path.isabs(path) else path
    resolved = os.path.realpath(candidate)
    if not is_within_root(resolved, root):
        raise ValueError("路径越出工作区边界")
    return resolved
