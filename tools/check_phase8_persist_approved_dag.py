#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.contracts.ids import IdKind
from ai.contracts.task_states import TaskState
from ai.orchestrator import (
    ApprovedDAGStore,
    ApprovedDAGValidationError,
    ProposedTask,
    TaskPlanProposal,
)
from ai.project.requirement_versions import RequirementVersionInput, RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import TASK_REGISTRY_DB_FILENAME, TaskCreateInput, TaskDAGValidator, TaskStore


def task(task_id: str, *, role: str = "requirements_engineer", deps: tuple[str, ...] = ()) -> ProposedTask:
    return ProposedTask(
        task_id=task_id,
        title=f"Approved plan {task_id}",
        role=role,
        priority=10,
        dependencies=deps,
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=True,
        max_attempts=3,
    )


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ruav-o8-8-")).resolve()
    projects = ProjectStore(root / "data" / "projects")
    handle = projects.create_project()
    RequirementVersionStore(projects).create_initial(
        handle.project_id,
        [RequirementVersionInput(text="Approved requirements for O8.8")],
        status=RequirementSetStatus.APPROVED,
    )

    # Seed one pre-existing TASK so candidate IDs demonstrably remain proposal
    # identities and are remapped to globally allocated persisted IDs.
    TaskStore(projects).create_task(
        handle.project_id,
        TaskCreateInput(
            title="Existing persisted task",
            role="requirements_engineer",
            priority=1,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema",),
            review_required=True,
            max_attempts=3,
        ),
    )

    proposal = TaskPlanProposal(
        project_id=handle.project_id,
        requirement_version=1,
        tasks=(
            task("TASK-000001"),
            task("TASK-000002", role="uav_systems_engineer", deps=("TASK-000001",)),
            task("TASK-000003", role="reviewer", deps=("TASK-000001", "TASK-000002")),
        ),
    )

    approved = ApprovedDAGStore(projects)
    receipt = approved.persist(proposal)
    mapping = receipt.mapping()
    if [item.value for item in receipt.task_ids] != ["TASK-000002", "TASK-000003", "TASK-000004"]:
        raise RuntimeError("candidate-to-persisted TASK remap drifted")

    tasks = TaskStore(projects)
    records = [tasks.open_task(handle.project_id, task_id) for task_id in receipt.task_ids]
    if any(record.status is not TaskState.CREATED or record.attempt != 0 for record in records):
        raise RuntimeError("O8.8 advanced task execution state")
    if tuple(item.value for item in records[2].dependencies) != (
        mapping["TASK-000001"].value,
        mapping["TASK-000002"].value,
    ):
        raise RuntimeError("dependency remap failed")

    report = TaskDAGValidator(projects).validate_project(handle.project_id)
    if report.node_count != 4 or report.edge_count != 3:
        raise RuntimeError("persisted DAG validation drifted")

    first_sequence = sqlite3.connect(projects.database_path).execute(
        "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
        (IdKind.TASK.value,),
    ).fetchone()[0]
    second = approved.persist(proposal)
    if second != receipt or len(tasks.list_tasks(handle.project_id)) != 4:
        raise RuntimeError("same-process idempotency failed")

    restarted_projects = ProjectStore(projects.projects_root)
    restarted = ApprovedDAGStore(restarted_projects).persist(proposal)
    if restarted != receipt:
        raise RuntimeError("restart idempotency failed")
    with sqlite3.connect(restarted_projects.database_path) as connection:
        second_sequence = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
            (IdKind.TASK.value,),
        ).fetchone()[0]
    if second_sequence != first_sequence:
        raise RuntimeError("idempotent restart allocated new TASK IDs")

    invalid = TaskPlanProposal(
        project_id=handle.project_id,
        requirement_version=1,
        tasks=(task("TASK-000010", role="future_specialist"),),
    )
    try:
        approved.persist(invalid)
    except ApprovedDAGValidationError:
        pass
    else:
        raise RuntimeError("invalid plan reached persistence")
    if len(tasks.list_tasks(handle.project_id)) != 4:
        raise RuntimeError("invalid plan mutated TaskStore")

    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        registry_tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    if registry_tables != ("task_dependency_edges", "task_registry_meta", "tasks"):
        raise RuntimeError("Task Registry schema drifted")
    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    if project_tables != PROJECT_SCHEMA_TABLES:
        raise RuntimeError("Project State schema drifted")

    print(
        "APPROVED_DAG_PERSISTENCE=PASS checks=10 proposed_tasks=3 persisted_tasks=3 "
        "id_remap=PASS dependencies=PASS statuses=CREATED idempotent=PASS restart=PASS "
        "invalid_plan_no_write=PASS requirement_binding=PASS registry_tables=3 "
        "project_schema_tables=3 task_execution=none provider_calls=none cp8=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
