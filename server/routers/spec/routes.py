"""spec 进展路由 — 解析工作区 spec 三件套的勾选状态供胶囊卡展示。"""

from __future__ import annotations

import os

from fastapi import APIRouter

from server.paths import project_root

router = APIRouter()

# spec 三件套固定路径约定（相对工作区根）
SPECS_DIR = ".agent/specs"
# 支持的清单文件：键为返回体分组名
CHECKLIST_FILES = {"tasks": "tasks.md", "checks": "checklist.md"}


def _parse_checklist(content: str) -> dict:
    """解析 markdown 勾选清单，返回 {total, done, items}。

    只收 `- [x] ` / `- [ ] ` 前缀的行；跳过 ```/~~~ 代码围栏内的行
    （三件套文档自身常嵌 checkbox 示例，纯前缀匹配会误收）。
    """
    items: list[dict] = []
    in_fence = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        lower = stripped.lower()
        if lower.startswith("- [x]"):
            items.append({"text": stripped[5:].strip(), "done": True})
        elif lower.startswith("- [ ]"):
            items.append({"text": stripped[5:].strip(), "done": False})
    done = sum(1 for it in items if it["done"])
    return {"total": len(items), "done": done, "items": items}


def _read_group(path: str) -> dict:
    """读单个清单文件并解析；文件缺失/读失败/编码异常按空清单兜底。"""
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_checklist(f.read())
    except (OSError, UnicodeDecodeError):
        return {"total": 0, "done": 0, "items": []}


@router.get("/api/spec/progress")
def spec_progress() -> dict:
    """返回当前工作区最近活跃 spec 的勾选进度。

    取 .agent/specs/ 下目录 mtime 最新的一个，解析其 tasks.md 与
    checklist.md 的 checkbox。工作区没有 spec 时返回 {"spec": null}；
    任何扫描/解析异常都降级为无 spec，不抛 500。
    """
    root = project_root()
    specs_root = os.path.join(root, SPECS_DIR)
    try:
        if not os.path.isdir(specs_root):
            return {"spec": None}
        entries = [
            name
            for name in os.listdir(specs_root)
            if os.path.isdir(os.path.join(specs_root, name))
        ]
        if not entries:
            return {"spec": None}
        latest = max(entries, key=lambda name: os.path.getmtime(os.path.join(specs_root, name)))
    except OSError:
        return {"spec": None}

    spec_dir = os.path.join(specs_root, latest)
    groups = {key: _read_group(os.path.join(spec_dir, filename)) for key, filename in CHECKLIST_FILES.items()}
    return {
        "spec": {"name": latest, "path": f"{SPECS_DIR}/{latest}"},
        "tasks": groups["tasks"],
        "checks": groups["checks"],
    }
