#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.contracts.ids import CanonicalId, IdKind
from ai.orchestrator import (
    ProposedTask,
    TaskPlanProposal,
    parse_task_plan_proposal_json,
    serialize_task_plan_proposal,
)


def task_id(n: int) -> CanonicalId:
    return CanonicalId.from_sequence(IdKind.TASK, n)


def main() -> int:
    # Deliberately include proposal content that later gates must reject/check:
    # duplicate task IDs, an unknown role/schema, a missing dependency, and
    # max_attempts above the frozen runtime policy. O8.1 must serialize these
    # structurally without turning them into executable tasks.
    tasks = (
        ProposedTask(
            task_id=task_id(1),
            title="Requirements analysis",
            role="requirements_engineer",
            priority=10,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema",),
            review_required=True,
            max_attempts=3,
        ),
        ProposedTask(
            task_id=task_id(2),
            title="Future specialist candidate",
            role="future_specialist",
            priority=20,
            dependencies=(task_id(999),),
            input_artifacts=(),
            output_schema="future.schema.v1",
            validators=("future_validator",),
            review_required=True,
            max_attempts=4,
        ),
        ProposedTask(
            task_id=task_id(2),
            title="Duplicate ID candidate",
            role="uav_systems_engineer",
            priority=30,
            dependencies=(task_id(1),),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema", "finite_values"),
            review_required=True,
            max_attempts=3,
        ),
    )
    proposal = TaskPlanProposal(
        project_id=CanonicalId.from_sequence(IdKind.PROJECT, 1),
        requirement_version=1,
        tasks=tasks,
    )

    encoded = serialize_task_plan_proposal(proposal)
    reopened = parse_task_plan_proposal_json(encoded)
    if reopened != proposal:
        raise RuntimeError("roundtrip mismatch")

    task_wire = reopened.tasks[0].to_dict()
    if "status" in task_wire or "attempt" in task_wire or "project_id" in task_wire:
        raise RuntimeError("proposal leaked executable task state")

    if [item.task_id.value for item in reopened.tasks].count("TASK-000002") != 2:
        raise RuntimeError("duplicate task IDs were normalized before PlanValidator")
    if reopened.tasks[1].role != "future_specialist":
        raise RuntimeError("unknown role was consumed before O8.4")
    if reopened.tasks[1].output_schema != "future.schema.v1":
        raise RuntimeError("unknown schema was consumed before O8.5")
    if reopened.tasks[1].dependencies[0].value != "TASK-000999":
        raise RuntimeError("missing dependency was consumed before O8.6")
    if reopened.tasks[1].max_attempts != 4:
        raise RuntimeError("retry policy was consumed before O8.7")

    print(
        "TASK_PLAN_PROPOSAL=PASS "
        "tasks=3 requirement_version=1 roundtrip=PASS runtime_state=absent "
        "duplicate_task_ids=deferred known_role=deferred known_schema=deferred "
        "dependency_validation=deferred resource_budget=deferred persistence=none "
        "task_execution=none provider_calls=none orchestrator_role=reserved_for_O8_2 "
        "plan_validator=reserved_for_O8_3 approved_dag=reserved_for_O8_8 cp8=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
