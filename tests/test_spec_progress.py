"""spec 进展端点测试 — 覆盖清单解析、代码围栏排除与无 spec 降级。

用 conftest 的 workspace fixture 把工作区切到 tmp_path，在隔离目录里
造 .agent/specs/<任务名>/ 三件套后直接调用路由函数断言解析结果。
"""

from __future__ import annotations

import os
import time

from server.routers.spec.routes import spec_progress


def _make_spec(workspace, name: str, tasks: str, checks: str | None) -> None:
    """在临时工作区造一个 spec 目录，checklist 传 None 表示不建该文件。"""
    spec_dir = workspace / ".agent" / "specs" / name
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# 大纲\n", encoding="utf-8")
    (spec_dir / "tasks.md").write_text(tasks, encoding="utf-8")
    if checks is not None:
        (spec_dir / "checklist.md").write_text(checks, encoding="utf-8")


def test_progress_parses_both_checklists(workspace):
    """正常解析：两份清单的 total/done/items 与文档勾选状态一致。"""
    _make_spec(
        workspace,
        "demo",
        tasks="# 任务\n\n- [x] 1.1 做完的事\n- [ ] 1.2 没做的事\n普通文本行\n",
        checks="# 验收\n\n- [x] 验收项A\n",
    )

    result = spec_progress()

    assert result["spec"]["name"] == "demo"
    assert result["spec"]["path"] == ".agent/specs/demo"
    assert result["tasks"]["total"] == 2
    assert result["tasks"]["done"] == 1
    assert result["tasks"]["items"][0] == {"text": "1.1 做完的事", "done": True}
    assert result["tasks"]["items"][1] == {"text": "1.2 没做的事", "done": False}
    assert result["checks"]["total"] == 1
    assert result["checks"]["done"] == 1


def test_progress_no_specs_returns_null(workspace):
    """工作区没有 .agent/specs/ 时返回 spec null 不报错。"""
    result = spec_progress()
    assert result == {"spec": None}


def test_progress_missing_checklist_returns_empty_group(workspace):
    """只有 tasks.md 没有 checklist.md 时，验证组为空清单。"""
    _make_spec(workspace, "solo", tasks="- [ ] 唯一任务\n", checks=None)

    result = spec_progress()

    assert result["tasks"]["total"] == 1
    assert result["checks"] == {"total": 0, "done": 0, "items": []}


def test_progress_skips_code_fence_and_non_checklist_lines(workspace):
    """代码围栏内的 checkbox 示例与非清单行都不误收。"""
    _make_spec(
        workspace,
        "fenced",
        tasks=(
            "# 标题不含条目\n"
            "## 普通小节\n"
            "1. 有序列表也不是条目\n"
            "- 普通无勾选列表行不是条目\n"
            "```\n- [x] 围栏内的示例不算\n- [ ] 围栏内也不算\n```\n"
            "~~~\n- [x] 波浪围栏同样不算\n~~~\n"
            "- [x] 围栏外的真条目\n"
        ),
        checks="",
    )

    result = spec_progress()

    assert result["tasks"]["total"] == 1
    assert result["tasks"]["items"][0]["text"] == "围栏外的真条目"
    # 空文件（只有空串内容）解析为 0 条
    assert result["checks"]["total"] == 0


def test_progress_tolerates_non_utf8_checklist(workspace):
    """清单文件编码异常（如 GBK）时按空清单降级，不抛 500。"""
    spec_dir = workspace / ".agent" / "specs" / "gbk"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# 大纲\n", encoding="utf-8")
    (spec_dir / "tasks.md").write_text("- [x] 中文任务\n", encoding="gbk")

    result = spec_progress()

    assert result["spec"]["name"] == "gbk"
    assert result["tasks"] == {"total": 0, "done": 0, "items": []}


def test_progress_picks_most_recent_spec(workspace):
    """多个 spec 并存时取目录 mtime 最新的一个。"""
    _make_spec(workspace, "old-spec", tasks="- [ ] 旧任务\n", checks="")
    _make_spec(workspace, "new-spec", tasks="- [ ] 新任务\n", checks="")
    old_dir = workspace / ".agent" / "specs" / "old-spec"
    new_dir = workspace / ".agent" / "specs" / "new-spec"
    old_stamp = time.time() - 600
    os.utime(old_dir, (old_stamp, old_stamp))
    os.utime(new_dir, None)

    result = spec_progress()

    assert result["spec"]["name"] == "new-spec"
    assert result["tasks"]["items"][0]["text"] == "新任务"
