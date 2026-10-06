#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.requirement_versions import (  # noqa: E402
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-requirement-versions-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        store = RequirementVersionStore(projects)

        first = store.create_initial(
            handle.project_id,
            [
                "Payload shall not exceed 2 kg.",
                "Minimum endurance shall be 20 minutes.",
            ],
        )
        keep = first.requirement_set.requirements[0].requirement_id

        second = store.append_version(
            handle.project_id,
            [
                RequirementVersionInput(
                    requirement_id=keep,
                    text="Payload shall not exceed 2.2 kg.",
                ),
                RequirementVersionInput(
                    text="The vehicle shall support return-to-home.",
                ),
            ],
        )

        old = store.open_version(handle.project_id, 1)
        restarted = RequirementVersionStore(
            ProjectStore(projects.projects_root)
        )
        latest = restarted.open_latest(handle.project_id)

        if old != first or latest != second:
            print("REQUIREMENT_VERSIONS=FAIL reason=reopen_mismatch")
            return 2

        print(
            "REQUIREMENT_VERSIONS=PASS "
            "versions=2 "
            f"latest={latest.version} "
            f"first_req={keep.value} "
            f"new_req={second.requirement_set.requirements[1].requirement_id.value} "
            "old_readable=PASS "
            "restart=PASS"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
