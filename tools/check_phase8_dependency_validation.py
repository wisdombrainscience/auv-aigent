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


def _task(task_id: str, *, dependencies: tuple[str, ...] = (), max_attempts: int = 3) -> ProposedTask:
    return ProposedTask(
        task_id=task_id,
        title=f"Plan candidate {task_id}",
        role="requirements_engineer",
        priority=10,
        dependencies=dependencies,
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=True,
        max_attempts=max_attempts,
    )


def _proposal(*tasks: ProposedTask) -> TaskPlanProposal:
    return TaskPlanProposal(project_id="UAV-PROJ-000001", requirement_version=1, tasks=tasks)


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
    valid = _proposal(
        _task("TASK-000001"),
        _task("TASK-000002", dependencies=("TASK-000001",)),
        _task("TASK-000003", dependencies=("TASK-000002",), max_attempts=3),
    )
    valid_result = DEFAULT_PLAN_VALIDATOR.validate(valid)
    if valid_result.overall is not ValidationStatus.PASS:
        raise RuntimeError("valid dependency chain should pass O8.6")
    if len(valid_result.checks) != 10:
        raise RuntimeError("cumulative PlanValidator check count drifted")
    for check_id in (
        "plan.dependency_existence",
        "plan.no_self_dependencies",
        "plan.dag_acyclic",
    ):
        if _check(valid_result, check_id).status is not ValidationStatus.PASS:
            raise RuntimeError(f"{check_id} should pass")

    missing = _proposal(_task("TASK-000001", dependencies=("TASK-000999",)))
    missing_result = DEFAULT_PLAN_VALIDATOR.validate(missing)
    if _check(missing_result, "plan.dependency_existence").status is not ValidationStatus.FAIL:
        raise RuntimeError("missing dependency was not rejected")

    self_dep = _proposal(_task("TASK-000001", dependencies=("TASK-000001",)))
    self_result = DEFAULT_PLAN_VALIDATOR.validate(self_dep)
    if _check(self_result, "plan.no_self_dependencies").status is not ValidationStatus.FAIL:
        raise RuntimeError("self dependency was not rejected")

    cycle = _proposal(
        _task("TASK-000003", dependencies=("TASK-000001",)),
        _task("TASK-000001", dependencies=("TASK-000002",)),
        _task("TASK-000002", dependencies=("TASK-000003",)),
    )
    cycle_result = DEFAULT_PLAN_VALIDATOR.validate(cycle)
    dag = _check(cycle_result, "plan.dag_acyclic")
    if dag.status is not ValidationStatus.FAIL:
        raise RuntimeError("dependency cycle was not rejected")
    if dag.details["cycle"] != (
        "TASK-000001", "TASK-000002", "TASK-000003", "TASK-000001"
    ):
        raise RuntimeError("cycle witness drifted")

    if _restart_result(cycle) != cycle_result.to_dict():
        raise RuntimeError("restart result drifted")

    print(
        "DEPENDENCY_VALIDATION=PASS cumulative_checks=10 dependency_checks=3 "
        "existence=PASS missing=FAIL self_dependency=FAIL cycle=FAIL "
        "cycle_witness=PASS restart=PASS resource_budget=active_O8_7 "
        "approved_dag=reserved_for_O8_8 persistence=none task_execution=none "
        "provider_calls=none cp8=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
