"""pytest 公共 fixture：把工作区根切到临时目录，测试结束后恢复。"""

from __future__ import annotations

import pytest

from server.paths import project_root, set_project_root


@pytest.fixture
def workspace(tmp_path):
    """把工作区根切到临时目录，测试结束后恢复原值。"""
    original = project_root()
    set_project_root(str(tmp_path))
    yield tmp_path
    set_project_root(original)
