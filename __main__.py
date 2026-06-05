"""common-code-py CLI entry point."""
import os
import sys

# 确保项目根目录在 sys.path 上
_project_dir = os.path.dirname(os.path.abspath(__file__))
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

from startup.entrypoints.cli import main

if __name__ == "__main__":
    main()
