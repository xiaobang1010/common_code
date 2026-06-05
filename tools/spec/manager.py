from __future__ import annotations

import re
from pathlib import Path

from .templates import checklist_template, spec_template, tasks_template

SPECS_DIR = ".agent/specs"


def spec_exists(project_root: str, change_id: str) -> bool:
    """Check if .agent/specs/<change-id>/ directory exists."""
    spec_path = Path(project_root) / SPECS_DIR / change_id
    return spec_path.is_dir()


def list_specs(project_root: str) -> list[dict]:
    """Scan .agent/specs/ and return list of {change_id, status} dicts."""
    specs_root = Path(project_root) / SPECS_DIR
    if not specs_root.is_dir():
        return []

    results: list[dict] = []
    for entry in sorted(specs_root.iterdir()):
        if not entry.is_dir():
            continue
        spec_file = entry / "spec.md"
        status = "draft"
        if spec_file.is_file():
            try:
                content = spec_file.read_text(encoding="utf-8")
                match = re.search(r"^##\s*Status\s*\n+(.+)$", content, re.MULTILINE)
                if match:
                    status = match.group(1).strip()
            except OSError:
                pass
        results.append({"change_id": entry.name, "status": status})
    return results


def create_spec(project_root: str, change_id: str, description: str) -> str:
    """Create .agent/specs/<change-id>/ with spec.md, tasks.md, checklist.md."""
    spec_path = Path(project_root) / SPECS_DIR / change_id
    spec_path.mkdir(parents=True, exist_ok=True)

    (spec_path / "spec.md").write_text(
        spec_template(change_id, description), encoding="utf-8"
    )
    (spec_path / "tasks.md").write_text(
        tasks_template(change_id, description), encoding="utf-8"
    )
    (spec_path / "checklist.md").write_text(
        checklist_template(change_id, description), encoding="utf-8"
    )

    return str(spec_path)


def update_spec(project_root: str, change_id: str, description: str) -> str:
    """Append new info to existing spec, update tasks and checklist."""
    spec_path = Path(project_root) / SPECS_DIR / change_id
    spec_file = spec_path / "spec.md"

    if not spec_file.is_file():
        return f"Spec not found: {change_id}"

    existing = spec_file.read_text(encoding="utf-8")

    # Append new description before the Status section
    status_pattern = re.compile(r"^##\s*Status\s*\n", re.MULTILINE)
    addition = f"\n### Update\n\n{description}\n"
    updated = status_pattern.sub(addition + "\n## Status\n", existing)

    spec_file.write_text(updated, encoding="utf-8")

    # Overwrite tasks.md and checklist.md with refreshed content
    (spec_path / "tasks.md").write_text(
        tasks_template(change_id, description), encoding="utf-8"
    )
    (spec_path / "checklist.md").write_text(
        checklist_template(change_id, description), encoding="utf-8"
    )

    return f"Updated spec '{change_id}': appended description, refreshed tasks and checklist"


def read_spec(project_root: str, change_id: str) -> str | None:
    """Read and return spec.md content, or None if not found."""
    spec_file = Path(project_root) / SPECS_DIR / change_id / "spec.md"
    if not spec_file.is_file():
        return None
    return spec_file.read_text(encoding="utf-8")
