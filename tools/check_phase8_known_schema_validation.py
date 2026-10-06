#!/usr/bin/env python3
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


def _task(task_id: str, *, output_schema: str = "engineering_task.v1", dependencies: tuple[str, ...] = (), max_attempts: int = 3) -> ProposedTask:
    return ProposedTask(
        task_id=task_id,
        title=f"Plan candidate {task_id}",
        role="requirements_engineer",
        priority=10,
        dependencies=dependencies,
        input_artifacts=(),
        output_schema=output_schema,
        validators=("schema",),
        review_required=True,
        max_attempts=max_attempts,
    )


def _check(result, check_id: str):
    return next(check for check in result.checks if check.check_id == check_id)


def _restart_result(proposal: TaskPlanProposal) -> dict[str, object]:
    payload = proposal.to_json_bytes().decode("utf-8")
    code = r'''
import json, sys
from ai.orchestrator import DEFAULT_PLAN_VALIDATOR, parse_task_plan_proposal_json
proposal = parse_task_plan_proposal_json(sys.argv[1].encode("utf-8"))
print(json.dumps(DEFAULT_PLAN_VALIDATOR.validate(proposal).to_dict(), sort_keys=True, separators=(",", ":")))
'''
    proc = subprocess.run([sys.executable, "-c", code, payload], cwd=str(_PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"restart validation failed: {proc.stdout}")
    return json.loads(proc.stdout)


def main() -> int:
    known = TaskPlanProposal(project_id="UAV-PROJ-000001", requirement_version=1, tasks=(_task("TASK-000001"),))
    known_result = DEFAULT_PLAN_VALIDATOR.validate(known)
    known_check = _check(known_result, "plan.known_output_schemas")
    if known_result.overall is not ValidationStatus.PASS or known_check.status is not ValidationStatus.PASS:
        raise RuntimeError("registered output schema should pass")
    if known_check.details["registered_schema_ids"] != ("engineering_task.v1",):
        raise RuntimeError("output schema registry drifted")

    unknown = TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=(
            _task("TASK-000001"),
            _task("TASK-000002", output_schema="future.schema.v1", dependencies=("TASK-000001",), max_attempts=4),
        ),
    )
    unknown_result = DEFAULT_PLAN_VALIDATOR.validate(unknown)
    unknown_check = _check(unknown_result, "plan.known_output_schemas")
    if unknown_result.overall is not ValidationStatus.FAIL or unknown_check.status is not ValidationStatus.FAIL:
        raise RuntimeError("unknown output schema was not rejected")
    if unknown_check.details["unknown_schemas"] != ("future.schema.v1",):
        raise RuntimeError("unknown-schema evidence drifted")
    if _restart_result(unknown) != unknown_result.to_dict():
        raise RuntimeError("restart result drifted")

    task = unknown.tasks[1]
    if task.dependencies[0].value != "TASK-000001":
        raise RuntimeError("dependency fixture drifted after O8.6")
    if task.max_attempts != 4:
        raise RuntimeError("resource-budget validation leaked into O8.5")

    print(
        "KNOWN_SCHEMA_VALIDATION=PASS "
        "check=plan.known_output_schemas registered_schemas=1 known=PASS unknown=FAIL "
        "unknown_schema=future.schema.v1 restart=PASS "
        "dependency_validation=active_O8_6 resource_budget=active_O8_7 "
        "approved_dag=reserved_for_O8_8 persistence=none task_execution=none "
        "provider_calls=none cp8=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
