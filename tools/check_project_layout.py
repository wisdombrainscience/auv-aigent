#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.layout import (  # noqa: E402
    PROJECT_DATA_DIRECTORIES,
    ProjectLayout,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-layout-") as raw:
        root = Path(raw) / "projects"

        first = ProjectLayout(
            projects_root=root,
            project_id="UAV-PROJ-000001",
        )
        first.ensure_directories()
        first.validate_directories()

        if first.project_json.exists():
            print("PROJECT_LAYOUT=FAIL reason=project_json_created_too_early")
            return 2

        reopened = ProjectLayout(
            projects_root=root,
            project_id="UAV-PROJ-000001",
        )
        reopened.validate_directories()

        if reopened.to_dict() != first.to_dict():
            print("PROJECT_LAYOUT=FAIL reason=restart_layout_mismatch")
            return 3

        print(
            "PROJECT_LAYOUT=PASS "
            f"dirs={len(PROJECT_DATA_DIRECTORIES)} "
            "project_json=reserved "
            "restart=PASS"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
