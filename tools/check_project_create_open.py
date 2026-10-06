#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-project-store-") as raw:
        projects_root = Path(raw) / "remote-uav" / "projects"

        first_store = ProjectStore(projects_root)
        first = first_store.create_project()

        restarted = ProjectStore(projects_root)
        opened = restarted.open_project(first.project_id.value)
        if opened.to_dict() != first.to_dict():
            print("PROJECT_CREATE_OPEN=FAIL reason=restart_mismatch")
            return 2

        second = restarted.create_project()
        if second.project_id.value != "UAV-PROJ-000002":
            print("PROJECT_CREATE_OPEN=FAIL reason=sequence_mismatch")
            return 3

        print(
            "PROJECT_CREATE_OPEN=PASS "
            f"first={first.project_id.value} "
            f"second={second.project_id.value} "
            "restart=PASS "
            f"db={restarted.database_path.name}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
