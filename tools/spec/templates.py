from __future__ import annotations


def spec_template(change_id: str, description: str) -> str:
    """Return spec.md content with standard sections."""
    return f"""# Spec: {change_id}

## Why

{description}

## What Changes

- [To be filled in]

## Impact

- [To be filled in]

## ADDED Requirements

- [To be filled in]

## Status

draft
"""


def tasks_template(change_id: str, description: str) -> str:
    """Return tasks.md content with placeholder tasks."""
    return f"""# Tasks: {change_id}

> {description}

## Tasks

- [ ] Define detailed requirements
- [ ] Implement changes
- [ ] Write tests
- [ ] Update documentation
"""


def checklist_template(change_id: str, description: str) -> str:
    """Return checklist.md content with placeholder items."""
    return f"""# Checklist: {change_id}

> {description}

## Pre-Implementation

- [ ] Requirements reviewed
- [ ] Design approved

## Implementation

- [ ] Code follows project conventions
- [ ] Edge cases handled

## Post-Implementation

- [ ] Tests pass
- [ ] Documentation updated
"""
