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


def _chain(task_count: int) -> TaskPlanProposal:
    tasks = []
    for index in range(1, task_count + 1):
        deps = () if index == 1 else (f"TASK-{index - 1:06d}",)
        tasks.append(_task(f"TASK-{index:06d}", dependencies=deps))
    return _proposal(*tasks)


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
    valid = _chain(13)  # 12 dependency edges, exactly at the frozen depth limit.
    valid_result = DEFAULT_PLAN_VALIDATOR.validate(valid)
    if valid_result.overall is not ValidationStatus.PASS:
        raise RuntimeError("valid resource-budget plan should pass")
    if len(valid_result.checks) != 10:
        raise RuntimeError("cumulative PlanValidator check count drifted")

    task_count_check = _check(valid_result, "plan.task_count_budget")
    depth_check = _check(valid_result, "plan.dependency_depth_budget")
    retry_check = _check(valid_result, "plan.retry_budget")
    runtime_check = _check(valid_result, "plan.runtime_resource_budget")
    if task_count_check.status is not ValidationStatus.PASS:
        raise RuntimeError("task-count budget should pass")
    if depth_check.status is not ValidationStatus.PASS or depth_check.details["observed_dependency_depth"] != 12:
        raise RuntimeError("depth budget boundary drifted")
    if retry_check.status is not ValidationStatus.PASS:
        raise RuntimeError("retry budget should pass")
    if runtime_check.status is not ValidationStatus.PASS:
        raise RuntimeError("frozen runtime resource policy drifted")

    too_many = _proposal(*tuple(_task(f"TASK-{index:06d}") for index in range(1, 32)))
    if _check(DEFAULT_PLAN_VALIDATOR.validate(too_many), "plan.task_count_budget").status is not ValidationStatus.FAIL:
        raise RuntimeError("31-task plan was not rejected")

    too_deep = _chain(14)  # 13 edges.
    if _check(DEFAULT_PLAN_VALIDATOR.validate(too_deep), "plan.dependency_depth_budget").status is not ValidationStatus.FAIL:
        raise RuntimeError("13-edge dependency chain was not rejected")

    retry = _proposal(_task("TASK-000001", max_attempts=4))
    if _check(DEFAULT_PLAN_VALIDATOR.validate(retry), "plan.retry_budget").status is not ValidationStatus.FAIL:
        raise RuntimeError("max_attempts=4 was not rejected")

    if _restart_result(valid) != valid_result.to_dict():
        raise RuntimeError("restart result drifted")

    print(
        "RESOURCE_BUDGET_VALIDATION=PASS cumulative_checks=10 resource_checks=4 "
        "max_tasks=30 task_count_over=FAIL max_dependency_depth=12 depth_over=FAIL "
        "max_attempts=3 retry_over=FAIL max_parallel_agents=1 max_loaded_models=1 "
        "task_timeout_s=300 runtime_budget=PASS restart=PASS "
        "approved_dag=reserved_for_O8_8 persistence=none task_execution=none "
        "provider_calls=none cp8=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
