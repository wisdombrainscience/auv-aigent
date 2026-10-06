#!/usr/bin/env python3
"""Cumulative compatibility probe for the Phase-8 PlanValidator boundary."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.contracts.validation_result import ValidationStatus
from ai.orchestrator import DEFAULT_PLAN_VALIDATOR, ProposedTask, TaskPlanProposal


def _task(task_id: str, *, role: str = "requirements_engineer") -> ProposedTask:
    return ProposedTask(
        task_id=task_id,
        title=f"Plan candidate {task_id}",
        role=role,
        priority=10,
        dependencies=(),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=True,
        max_attempts=3,
    )


def _restart_result(proposal: TaskPlanProposal) -> dict[str, object]:
    payload = proposal.to_json_bytes().decode("utf-8")
    code = r'''
import json, sys
from ai.orchestrator import DEFAULT_PLAN_VALIDATOR, parse_task_plan_proposal_json
proposal = parse_task_plan_proposal_json(sys.argv[1].encode("utf-8"))
print(json.dumps(DEFAULT_PLAN_VALIDATOR.validate(proposal).to_dict(), sort_keys=True, separators=(",", ":")))
'''
    proc = subprocess.run(
        [sys.executable, "-c", code, payload],
        cwd=str(_PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"restart validation failed: {proc.stdout}")
    return json.loads(proc.stdout)


def main() -> int:
    valid = TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=(_task("TASK-000001"), _task("TASK-000002")),
    )
    valid_result = DEFAULT_PLAN_VALIDATOR.validate(valid)
    if valid_result.overall is not ValidationStatus.PASS:
        raise RuntimeError("baseline plan should pass cumulative O8.3-O8.7")
    if tuple(check.check_id for check in valid_result.checks)[:1] != (
        "plan.unique_task_ids",
    ):
        raise RuntimeError("unique-task-ID check order drifted")

    duplicate = TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=(_task("TASK-000001"), _task("TASK-000001")),
    )
    duplicate_result = DEFAULT_PLAN_VALIDATOR.validate(duplicate)
    unique = next(
        check for check in duplicate_result.checks
        if check.check_id == "plan.unique_task_ids"
    )
    if unique.status is not ValidationStatus.FAIL:
        raise RuntimeError("duplicate task IDs were not rejected")
    if unique.details["duplicate_task_ids"] != ("TASK-000001",):
        raise RuntimeError("duplicate-task evidence is not deterministic")

    if _restart_result(valid) != valid_result.to_dict():
        raise RuntimeError("restart result drifted")

    print(
        "PLAN_VALIDATOR=PASS "
        f"checks={len(valid_result.checks)} unique_task_ids=PASS "
        "duplicate_task_ids=FAIL restart=PASS "
        "known_role=active_O8_4 known_schema=active_O8_5 "
        "dependency_validation=active_O8_6 "
        "resource_budget=active_O8_7 approved_dag=reserved_for_O8_8 "
        "persistence=none task_execution=none provider_calls=none cp8=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
