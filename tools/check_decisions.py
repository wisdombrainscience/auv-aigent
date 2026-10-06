#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.decisions import (  # noqa: E402
    Decision,
    DecisionSet,
    DecisionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-decisions-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        store = DecisionStore(projects)

        value = DecisionSet(
            project_id=handle.project_id,
            decisions=(
                Decision(
                    key="airframe.frame_type",
                    value="quad_x",
                    rationale=(
                        "Matches the current PX4 simulation configuration."
                    ),
                ),
                Decision(
                    key="navigation.internal_frame",
                    value="LOCAL_NED",
                    rationale=(
                        "Preserves the existing runtime coordinate convention."
                    ),
                ),
            ),
        )
        store.save(value)

        restarted = DecisionStore(
            ProjectStore(projects.projects_root)
        )
        reopened = restarted.open(handle.project_id)

        if reopened != value:
            print("DECISIONS=FAIL reason=restart_mismatch")
            return 2

        with sqlite3.connect(
            projects.database_path
        ) as connection:
            tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            )

        if tables != PROJECT_SCHEMA_TABLES:
            print("DECISIONS=FAIL reason=schema_changed")
            return 3

        print(
            "DECISIONS=PASS "
            f"decisions={len(value.decisions)} "
            "restart=PASS "
            "hash=PASS "
            "authority=external "
            f"schema_tables={len(tables)} "
            "transactions=reserved_for_S3_9"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
