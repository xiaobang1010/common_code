"""搜索相关路由：ripgrep 全局搜索。"""

from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter

from server.paths import project_root

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/search - 全局搜索
# ---------------------------------------------------------------------------


@router.get("/api/search")
async def search(
    q: str,
    case_sensitive: bool = False,
    regex: bool = False,
) -> dict:
    """全局搜索接口，用 ripgrep 搜索项目文件内容。

    参数 q：搜索关键词。
    参数 case_sensitive：是否区分大小写，默认 false。
    参数 regex：是否按正则匹配，默认 false。
    返回 {"results": [{"path", "line_number", "line", "matches"}]}。
    rg 不存在或调用失败时返回空结果和 error 字段。
    """
    root = project_root()

    # 构建 rg 命令：JSON 输出、带行号、每文件最多 50 个匹配
    cmd: list[str] = ["rg", "--json", "-n", "--max-count", "50"]

    # 默认不区分大小写；区分大小写时不加 -i
    if not case_sensitive:
        cmd.append("-i")

    # 非正则模式按字面字符串匹配
    if not regex:
        cmd.append("--fixed-strings")

    # 排除常见无关目录
    cmd.extend(
        [
            "-g", "!node_modules",
            "-g", "!__pycache__",
            "-g", "!.git",
            "-g", "!dist",
        ]
    )

    cmd.append(q)
    cmd.append(".")

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return {"results": [], "error": "ripgrep not found"}

    # rg --json 每行输出一个 JSON 对象，只取 type=="match" 的
    results: list[dict] = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data", {})
        path = data.get("path", {}).get("text", "")
        line_number = data.get("line_number", 0)
        line_text = data.get("lines", {}).get("text", "")
        submatches = data.get("submatches", [])
        matches = [
            {"start": sm.get("start", 0), "end": sm.get("end", 0)}
            for sm in submatches
        ]
        results.append(
            {
                "path": path,
                "line_number": line_number,
                "line": line_text,
                "matches": matches,
            }
        )

    return {"results": results}
