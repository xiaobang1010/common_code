"""git 忽略判定：批量查工作区里的哪些条目被 .gitignore 规则忽略。

判定交给 git 自己算，前端只需要一个「命中/未命中」的答案：嵌套 .gitignore、
取反规则、父目录被整目录忽略这三种情况都由 git 处理，调用方不必自己推导前缀。
"""

from __future__ import annotations

import subprocess

# 统一的 git 全局参数：关闭路径转义，中文文件名原样输出（配 utf-8 解码）
GIT_GLOBAL_ARGS = ["-c", "core.quotepath=false"]
# 统一的子进程文本解码选项：git 输出为 utf-8 字节，Windows 默认本地编码会乱码
GIT_TEXT_OPTS = {"encoding": "utf-8", "errors": "replace"}

# 批量判定的超时上限（秒）：忽略标记只是展示信息，取不到就不标灰，不允许拖慢列目录
_CHECK_TIMEOUT = 2


def ignored_names(root: str, paths: list[str]) -> set[str]:
    """批量判断 paths（工作区相对口径）里哪些被 git 忽略，返回命中的子集。

    一次 `git check-ignore --stdin` 判完整批，避免逐条目起进程。判定看索引：
    被跟踪的文件即使命中忽略模式也不算忽略，因此内含被跟踪文件的目录不会整体
    被判为忽略（其下真正被忽略的子目录仍会命中）。
    非 git 仓库、git 不可用、超时一律返回空集，调用方按「都没有被忽略」处理。
    """
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, "check-ignore", "--stdin"],
            cwd=root,
            # 按字节喂 stdin：文本模式在 Windows 下会把换行翻成 \r\n，git 会把 \r
            # 当成路径的一部分，全部路径都判不中（表现为「什么都没被忽略」）
            input=("\n".join(paths) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=_CHECK_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return set()
    # 无命中时 git 返回 1，非 git 仓库返回 128，都按「都没被忽略」处理
    if proc.returncode != 0:
        return set()
    stdout = proc.stdout.decode("utf-8", errors="replace")
    return {line for line in stdout.splitlines() if line}
