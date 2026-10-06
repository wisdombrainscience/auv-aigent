#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.facts import Fact, FactSet, FactStore  # noqa: E402
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-facts-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        store = FactStore(projects)
        value = FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(key="vehicle.mass_kg", value=2.5, provenance="USER_FACT"),
                Fact(key="vehicle.payload_kg", value=0.6, provenance="USER_FACT"),
                Fact(key="physics.gravity_m_s2", value=9.80665, provenance="VERIFIED_FACT"),
            ),
        )
        store.save(value)

        restarted = FactStore(ProjectStore(projects.projects_root))
        reopened = restarted.open(handle.project_id)
        if reopened != value:
            print("FACTS=FAIL reason=restart_mismatch")
            return 2

        with sqlite3.connect(projects.database_path) as connection:
            tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        if tables != PROJECT_SCHEMA_TABLES:
            print("FACTS=FAIL reason=schema_changed")
            return 3

        user = sum(1 for item in reopened.facts if item.provenance.value == "USER_FACT")
        verified = sum(1 for item in reopened.facts if item.provenance.value == "VERIFIED_FACT")
        print(
            "FACTS=PASS "
            f"facts={len(reopened.facts)} "
            f"user={user} "
            f"verified={verified} "
            "restart=PASS hash=PASS schema_tables=3"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
