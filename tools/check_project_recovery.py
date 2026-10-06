#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import ai.project.transaction as transaction_module  # noqa: E402
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
from ai.project.requirement_versions import (  # noqa: E402
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402
from ai.project.transaction import (  # noqa: E402
    PROJECT_TRANSACTION_JOURNAL_FILENAME,
    ProjectStateTransaction,
    recover_project_state_transaction,
)


class SimulatedCrash(BaseException):
    pass


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-restart-recovery-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()

        reqs = RequirementVersionStore(projects)
        v1 = reqs.create_initial(handle.project_id, ["A"])
        v2 = reqs.append_version(
            handle.project_id,
            [
                RequirementVersionInput(
                    requirement_id=v1.requirement_set.requirements[0].requirement_id,
                    text="A2",
                ),
                RequirementVersionInput(text="B"),
            ],
        )

        old_facts = FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(
                    key="vehicle.mass_kg",
                    value=2.0,
                    provenance="USER_FACT",
                ),
            ),
        )
        old_assumptions = AssumptionSet(
            project_id=handle.project_id,
            assumptions=(
                Assumption(
                    key="airframe.drag_coefficient",
                    value=0.7,
                ),
            ),
        )
        old_decisions = DecisionSet(
            project_id=handle.project_id,
            decisions=(
                Decision(
                    key="navigation.internal_frame",
                    value="LOCAL_NED",
                    rationale="Established project convention.",
                ),
            ),
        )

        tx = ProjectStateTransaction(projects, handle.project_id)
        tx.stage_facts(old_facts)
        tx.stage_assumptions(old_assumptions)
        tx.stage_decisions(old_decisions)
        tx.commit()

        interrupted = ProjectStateTransaction(
            projects,
            handle.project_id,
        )
        interrupted.stage_facts(
            FactSet(
                project_id=handle.project_id,
                facts=(
                    Fact(
                        key="vehicle.mass_kg",
                        value=3.0,
                        provenance="USER_FACT",
                    ),
                ),
            )
        )
        interrupted.stage_assumptions(
            AssumptionSet(
                project_id=handle.project_id,
                assumptions=(
                    Assumption(
                        key="airframe.drag_coefficient",
                        value=0.9,
                    ),
                ),
            )
        )
        interrupted.stage_decisions(
            DecisionSet(
                project_id=handle.project_id,
                decisions=(
                    Decision(
                        key="navigation.internal_frame",
                        value="BODY_FRD",
                        rationale="Simulated interrupted change.",
                    ),
                ),
            )
        )

        original = transaction_module._atomic_replace
        target_writes = 0

        def crash_mid_commit(path, data):
            nonlocal target_writes
            if path.name != PROJECT_TRANSACTION_JOURNAL_FILENAME:
                target_writes += 1
                if target_writes == 4:
                    raise SimulatedCrash()
            return original(path, data)

        transaction_module._atomic_replace = crash_mid_commit
        try:
            try:
                interrupted.commit()
                print("PROJECT_RECOVERY=FAIL reason=crash_not_injected")
                return 2
            except SimulatedCrash:
                pass
        finally:
            transaction_module._atomic_replace = original

        restarted = ProjectStore(projects.projects_root)
        if not recover_project_state_transaction(
            restarted,
            handle.project_id,
        ):
            print("PROJECT_RECOVERY=FAIL reason=no_recovery")
            return 3

        if FactStore(restarted).open(handle.project_id) != old_facts:
            print("PROJECT_RECOVERY=FAIL reason=facts")
            return 4
        if AssumptionStore(restarted).open(handle.project_id) != old_assumptions:
            print("PROJECT_RECOVERY=FAIL reason=assumptions")
            return 5
        if DecisionStore(restarted).open(handle.project_id) != old_decisions:
            print("PROJECT_RECOVERY=FAIL reason=decisions")
            return 6

        reqs_after = RequirementVersionStore(restarted)
        if reqs_after.open_version(handle.project_id, 1) != v1:
            print("PROJECT_RECOVERY=FAIL reason=requirement_v1")
            return 7
        if reqs_after.open_latest(handle.project_id) != v2:
            print("PROJECT_RECOVERY=FAIL reason=requirement_latest")
            return 8

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
            print("PROJECT_RECOVERY=FAIL reason=schema_changed")
            return 9

        print(
            "PROJECT_RECOVERY=PASS "
            "restart=PASS "
            "interrupted_commit=ROLLED_BACK "
            "requirements=PASS "
            "facts=PASS "
            "assumptions=PASS "
            "decisions=PASS "
            "journal=cleared "
            f"schema_tables={len(tables)}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
