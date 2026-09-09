"""spec 进展路由 — 解析工作区清单（spec 三件套与 todos 轻清单）的勾选状态供概要卡/胶囊卡展示。"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter

import server.state
from server.paths import project_root

router = APIRouter()

# spec 三件套固定路径约定（相对工作区根）
SPECS_DIR = ".agent/specs"
# 轻清单固定路径约定：todos 目录下平铺单文件 <名字>.md
TODOS_DIR = ".agent/todos"
# 支持的清单文件：键为返回体分组名
CHECKLIST_FILES = {"tasks": "tasks.md", "checks": "checklist.md"}

# 会话归属识别：从工具调用参数里匹配清单路径片段。
# 参数本身是 JSON 字符串，路径分隔符两种风格都可能出现（写盘多用正斜杠、
# 用户提供路径可能是反斜杠），名字段排除分隔符/引号/空白/冒号
SPEC_REF_PATTERN = re.compile(r"\.agent[/\\]+specs[/\\]+([^/\\\"'\s:]+)[/\\]")
# 轻清单引用：名字不含扩展名，其余排除项同上
TODO_REF_PATTERN = re.compile(r"\.agent[/\\]+todos[/\\]+([^/\\\"'\s:]+?)\.md")


def _session_spec_name(messages: list[dict]) -> str | None:
    """识别会话归属的清单名（spec 目录名或 todo 文件名）：最后出现的一个。

    只扫 tool_calls 的 arguments（AI 实际读写的路径），不扫消息正文——
    正文里提到别的清单路径不构成归属。识别不出返回 None。
    """
    name: str | None = None
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            args = str((call.get("function") or {}).get("arguments") or "")
            matches = SPEC_REF_PATTERN.findall(args) + TODO_REF_PATTERN.findall(args)
            if matches:
                name = matches[-1]
    return name


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


def _read_spec_groups(spec_dir: str, name: str) -> dict:
    """读单个 spec 目录的两份清单，拼成完整返回体（kind=spec）。"""
    groups = {key: _read_group(os.path.join(spec_dir, filename)) for key, filename in CHECKLIST_FILES.items()}
    return {
        "kind": "spec",
        "spec": {"name": name, "path": f"{SPECS_DIR}/{name}"},
        "tasks": groups["tasks"],
        "checks": groups["checks"],
    }


def _read_todo_groups(todo_file: str, name: str) -> dict:
    """读单个 todo 轻清单文件，拼成完整返回体：只有任务清单，无验证组。"""
    return {
        "kind": "todo",
        "spec": {"name": name, "path": f"{TODOS_DIR}/{name}.md"},
        "tasks": _read_group(todo_file),
        "checks": {"total": 0, "done": 0, "items": []},
    }


@router.get("/api/spec/progress")
def spec_progress(session_id: str = "") -> dict:
    """返回清单的勾选进度；传 session_id 时精确到该会话归属的清单。

    清单有两种：spec 三件套目录（.agent/specs/<名字>/）与 todos 轻清单
    单文件（.agent/todos/<名字>.md），返回体用 kind 字段区分。会话归属
    按优先级取：会话行上记录的清单名（AI 写 .agent/specs/<名字>/ 或
    .agent/todos/<名字>.md 时由文件事件钩子即时记录）> 会话消息工具调用
    里最后出现的清单路径（兜底覆盖历史已落库会话）。拿到名字后先查 spec
    目录、再查 todo 文件（同名 spec 优先），两者都不存在时返回
    {"spec": null}——同一工作区多会话并存时不拿别的会话的进展充数。
    不传 session_id 维持工作区口径：specs 子目录与 todos 平铺文件合并按
    mtime 取最新。

    工作区没有任何清单时返回 {"spec": null}；任何扫描/解析异常都降级为
    无清单，不抛 500。
    """
    root = project_root()

    # 会话口径：归属明确，识别不出就明确为无，不回退到工作区最近活跃
    if session_id:
        store = server.state.session_store
        # 写盘记录优先（任务进行中消息未落库时也能归属），消息识别兜底历史会话
        name = store.get_session_spec(session_id)
        if not name:
            session = store.get_session(session_id)
            name = _session_spec_name(session.messages) if session else None
        if not name:
            return {"spec": None}
        # 同名时 spec 目录优先于 todo 文件
        spec_dir = os.path.join(root, SPECS_DIR, name)
        if os.path.isdir(spec_dir):
            return _read_spec_groups(spec_dir, name)
        todo_file = os.path.join(root, TODOS_DIR, f"{name}.md")
        if os.path.isfile(todo_file):
            return _read_todo_groups(todo_file, name)
        return {"spec": None}

    # 工作区口径（不传 session_id 的旧调用方）：specs 子目录与 todos 平铺
    # 文件合并按 mtime 取最新
    candidates: list[tuple[float, str, str]] = []  # (mtime, kind, name)
    specs_root = os.path.join(root, SPECS_DIR)
    todos_root = os.path.join(root, TODOS_DIR)
    try:
        if os.path.isdir(specs_root):
            candidates.extend(
                (os.path.getmtime(os.path.join(specs_root, entry)), "spec", entry)
                for entry in os.listdir(specs_root)
                if os.path.isdir(os.path.join(specs_root, entry))
            )
        if os.path.isdir(todos_root):
            candidates.extend(
                (os.path.getmtime(os.path.join(todos_root, filename)), "todo", filename[:-3])
                for filename in os.listdir(todos_root)
                if filename.endswith(".md") and os.path.isfile(os.path.join(todos_root, filename))
            )
        if not candidates:
            return {"spec": None}
        _, kind, name = max(candidates)
    except OSError:
        return {"spec": None}

    if kind == "spec":
        return _read_spec_groups(os.path.join(specs_root, name), name)
    return _read_todo_groups(os.path.join(todos_root, f"{name}.md"), name)
