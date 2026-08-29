"""spec 进展路由 — 解析工作区 spec 三件套的勾选状态供胶囊卡展示。"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter

import server.state
from server.paths import project_root

router = APIRouter()

# spec 三件套固定路径约定（相对工作区根）
SPECS_DIR = ".agent/specs"
# 支持的清单文件：键为返回体分组名
CHECKLIST_FILES = {"tasks": "tasks.md", "checks": "checklist.md"}

# 会话归属识别：从工具调用参数里匹配 .agent/specs/<名字>/ 路径片段。
# 参数本身是 JSON 字符串，路径分隔符两种风格都可能出现（写盘多用正斜杠、
# 用户提供路径可能是反斜杠），名字段排除分隔符/引号/空白/冒号
SPEC_REF_PATTERN = re.compile(r"\.agent[/\\]+specs[/\\]+([^/\\\"'\s:]+)[/\\]")


def _session_spec_name(messages: list[dict]) -> str | None:
    """识别会话归属的 spec 目录名：该会话工具调用里最后出现的一个。

    只扫 tool_calls 的 arguments（AI 实际读写的路径），不扫消息正文——
    正文里提到别的 spec 路径不构成归属。识别不出返回 None。
    """
    name: str | None = None
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            args = str((call.get("function") or {}).get("arguments") or "")
            matches = SPEC_REF_PATTERN.findall(args)
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
    """读单个 spec 目录的两份清单，拼成完整返回体。"""
    groups = {key: _read_group(os.path.join(spec_dir, filename)) for key, filename in CHECKLIST_FILES.items()}
    return {
        "spec": {"name": name, "path": f"{SPECS_DIR}/{name}"},
        "tasks": groups["tasks"],
        "checks": groups["checks"],
    }


@router.get("/api/spec/progress")
def spec_progress(session_id: str = "") -> dict:
    """返回 spec 三件套的勾选进度；传 session_id 时精确到该会话归属的 spec。

    会话归属按优先级取：会话行上记录的 spec_name（AI 写 .agent/specs/<名字>/
    时由文件事件钩子即时记录）> 会话消息工具调用里最后出现的 spec 路径
    （兜底覆盖历史已落库会话）。两者都识别不出（新会话、没碰过 spec 的
    会话、会话不存在）或对应目录已删除时返回 {"spec": null}——同一工作区
    多会话并存时不拿别的会话的进展充数。不传 session_id 维持旧行为：
    工作区内目录 mtime 最新的 spec。

    工作区没有 .agent/specs/ 时返回 {"spec": null}；任何扫描/解析异常都
    降级为无 spec，不抛 500。
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
        spec_dir = os.path.join(root, SPECS_DIR, name)
        if not os.path.isdir(spec_dir):
            return {"spec": None}
        return _read_spec_groups(spec_dir, name)

    # 工作区口径（不传 session_id 的旧调用方）：取 mtime 最新的一个
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

    return _read_spec_groups(os.path.join(specs_root, latest), latest)
