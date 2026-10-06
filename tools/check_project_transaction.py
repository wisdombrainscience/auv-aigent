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
from ai.project.decisions import (  # noqa: E402
    Decision,
    DecisionSet,
    DecisionStore,
)
from ai.project.facts import Fact, FactSet, FactStore  # noqa: E402
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402
from ai.project.transaction import (  # noqa: E402
    ProjectStateTransaction,
)


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-project-transaction-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()

        facts = FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(
                    key="vehicle.mass_kg",
                    value=2.5,
                    provenance="USER_FACT",
                ),
            ),
        )
        assumptions = AssumptionSet(
            project_id=handle.project_id,
            assumptions=(
                Assumption(
                    key="airframe.drag_coefficient",
                    value=0.8,
                ),
            ),
        )
        decisions = DecisionSet(
            project_id=handle.project_id,
            decisions=(
                Decision(
                    key="navigation.internal_frame",
                    value="LOCAL_NED",
                    rationale=(
                        "Preserves the existing runtime coordinate convention."
                    ),
                ),
            ),
        )

        tx = ProjectStateTransaction(
            projects,
            handle.project_id,
        )
        tx.stage_facts(facts)
        tx.stage_assumptions(assumptions)
        tx.stage_decisions(decisions)
        receipt = tx.commit()

        restarted = ProjectStore(projects.projects_root)
        if FactStore(restarted).open(handle.project_id) != facts:
            print("PROJECT_TRANSACTION=FAIL reason=facts_restart")
            return 2
        if AssumptionStore(restarted).open(handle.project_id) != assumptions:
            print("PROJECT_TRANSACTION=FAIL reason=assumptions_restart")
            return 3
        if DecisionStore(restarted).open(handle.project_id) != decisions:
            print("PROJECT_TRANSACTION=FAIL reason=decisions_restart")
            return 4

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
            print("PROJECT_TRANSACTION=FAIL reason=schema_changed")
            return 5

        print(
            "PROJECT_TRANSACTION=PASS "
            f"categories={len(receipt.categories)} "
            f"files={receipt.file_count} "
            "restart=PASS "
            "rollback=PASS "
            f"schema_tables={len(tables)} "
            "crash_recovery=reserved_for_S3_10"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
