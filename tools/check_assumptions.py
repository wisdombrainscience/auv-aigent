#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.assumptions import (  # noqa: E402
    Assumption,
    AssumptionSet,
    AssumptionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-assumptions-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        store = AssumptionStore(projects)

        value = AssumptionSet(
            project_id=handle.project_id,
            assumptions=(
                Assumption(
                    key="airframe.drag_coefficient",
                    value=0.8,
                ),
                Assumption(
                    key="battery.usable_fraction",
                    value=0.8,
                ),
            ),
        )
        store.save(value)

        restarted = AssumptionStore(
            ProjectStore(projects.projects_root)
        )
        reopened = restarted.open(handle.project_id)
        if reopened != value:
            print(
                "ASSUMPTIONS=FAIL "
                "reason=restart_mismatch"
            )
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
            print(
                "ASSUMPTIONS=FAIL "
                "reason=schema_changed"
            )
            return 3

        print(
            "ASSUMPTIONS=PASS "
            f"assumptions={len(value.assumptions)} "
            "provenance=ENGINEERING_ASSUMPTION "
            "restart=PASS "
            "hash=PASS "
            f"schema_tables={len(tables)}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
