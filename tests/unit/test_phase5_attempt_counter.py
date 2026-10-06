from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import sqlite3

import pytest

from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    TASK_ATTEMPT_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskAttemptConflictError,
    TaskAttemptStore,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskPriorityQueueConflictError,
    TaskReadinessResolver,
    TaskStore,
    TaskTransitionConflictError,
)


def make_project(
    tmp_path: Path,
    *,
    max_attempts: int = 3,
):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    tasks = TaskStore(projects)
    record = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Attempt-bounded engineering task",
            role="uav_systems_engineer",
            priority=100,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema",),
            review_required=False,
            max_attempts=max_attempts,
        ),
    )
    lifecycle = TaskLifecycleStore(projects)
    attempts = TaskAttemptStore(projects)
    readiness = TaskReadinessResolver(projects)
    queue = TaskPriorityQueue(projects)
    return (
        projects,
        handle,
        tasks,
        record,
        lifecycle,
        attempts,
        readiness,
        queue,
    )


def queue_and_assign(
    handle,
    record,
    lifecycle,
    readiness,
    queue,
):
    readiness.resolve_project(handle.project_id)
    queue.enqueue_ready(handle.project_id)
    return lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.ASSIGNED,
    )


def finish_to_rework(
    handle,
    task_id,
    lifecycle,
):
    lifecycle.transition_task(
        handle.project_id,
        task_id,
        TaskState.RESULT_PROPOSED,
    )
    return lifecycle.transition_task(
        handle.project_id,
        task_id,
        TaskState.REWORK,
    )


def test_contract_version_is_explicit() -> None:
    assert TASK_ATTEMPT_CONTRACT_VERSION == "1.0"


def test_initial_budget_is_zero_of_max(tmp_path: Path) -> None:
    (
        _projects,
        handle,
        _tasks,
        record,
        _lifecycle,
        attempts,
        _readiness,
        _queue,
    ) = make_project(tmp_path)

    budget = attempts.budget(
        handle.project_id,
        record.task_id,
    )

    assert budget.attempt == 0
    assert budget.max_attempts == 3
    assert budget.remaining_attempts == 3
    assert budget.exhausted is False


def test_assigned_to_running_increments_exactly_once(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        attempts,
        readiness,
        queue,
    ) = make_project(tmp_path)

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    running = lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )

    assert running.status is TaskState.RUNNING
    assert running.attempt == 1
    assert attempts.budget(
        handle.project_id,
        record.task_id,
    ).remaining_attempts == 2
    assert tasks.open_task(
        handle.project_id,
        record.task_id,
    ).attempt == 1


def test_queue_and_assignment_do_not_increment_attempt(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        _attempts,
        readiness,
        queue,
    ) = make_project(tmp_path)

    assigned = queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )

    assert assigned.status is TaskState.ASSIGNED
    assert assigned.attempt == 0
    assert tasks.open_task(
        handle.project_id,
        record.task_id,
    ).attempt == 0


def test_non_running_lifecycle_transitions_do_not_increment_attempt(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        _attempts,
        readiness,
        queue,
    ) = make_project(tmp_path)

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )
    proposed = lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RESULT_PROPOSED,
    )
    rework = lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.REWORK,
    )

    assert proposed.attempt == 1
    assert rework.attempt == 1
    assert tasks.open_task(
        handle.project_id,
        record.task_id,
    ).attempt == 1


def test_rework_with_budget_routes_to_blocked(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        attempts,
        readiness,
        queue,
    ) = make_project(tmp_path)

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )
    finish_to_rework(
        handle,
        record.task_id,
        lifecycle,
    )

    route = attempts.route_rework(
        handle.project_id,
        record.task_id,
    )

    assert route.attempt == 1
    assert route.max_attempts == 3
    assert route.target_status is TaskState.BLOCKED
    assert tasks.open_task(
        handle.project_id,
        record.task_id,
    ).status is TaskState.BLOCKED

    readiness.resolve_project(handle.project_id)
    assert tasks.open_task(
        handle.project_id,
        record.task_id,
    ).status is TaskState.READY


def test_exhausted_rework_routes_to_needs_human(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        attempts,
        readiness,
        queue,
    ) = make_project(
        tmp_path,
        max_attempts=1,
    )

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )
    finish_to_rework(
        handle,
        record.task_id,
        lifecycle,
    )

    route = attempts.route_rework(
        handle.project_id,
        record.task_id,
    )

    assert route.attempt == 1
    assert route.max_attempts == 1
    assert route.target_status is TaskState.NEEDS_HUMAN
    reopened = tasks.open_task(
        handle.project_id,
        record.task_id,
    )
    assert reopened.status is TaskState.NEEDS_HUMAN
    assert reopened.attempt == 1


def test_three_attempts_are_bounded_then_human_gate(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        attempts,
        readiness,
        queue,
    ) = make_project(
        tmp_path,
        max_attempts=3,
    )

    observed = []
    for attempt_number in (1, 2, 3):
        queue_and_assign(
            handle,
            record,
            lifecycle,
            readiness,
            queue,
        )
        running = lifecycle.transition_task(
            handle.project_id,
            record.task_id,
            TaskState.RUNNING,
        )
        observed.append(running.attempt)
        finish_to_rework(
            handle,
            record.task_id,
            lifecycle,
        )
        route = attempts.route_rework(
            handle.project_id,
            record.task_id,
        )
        if attempt_number < 3:
            assert route.target_status is TaskState.BLOCKED
        else:
            assert route.target_status is TaskState.NEEDS_HUMAN

    assert observed == [1, 2, 3]
    final = tasks.open_task(
        handle.project_id,
        record.task_id,
    )
    assert final.attempt == 3
    assert final.max_attempts == 3
    assert final.status is TaskState.NEEDS_HUMAN
    assert attempts.budget(
        handle.project_id,
        record.task_id,
    ).exhausted is True


def test_exhausted_queued_task_cannot_be_assigned(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        _attempts,
        readiness,
        queue,
    ) = make_project(
        tmp_path,
        max_attempts=1,
    )

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.INTERRUPTED,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.READY,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.QUEUED,
    )

    with pytest.raises(
        TaskTransitionConflictError,
        match="exhausted attempt budget",
    ):
        lifecycle.transition_task(
            handle.project_id,
            record.task_id,
            TaskState.ASSIGNED,
        )

    reopened = tasks.open_task(
        handle.project_id,
        record.task_id,
    )
    assert reopened.status is TaskState.QUEUED
    assert reopened.attempt == 1


def test_priority_queue_rejects_exhausted_queued_task(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        record,
        lifecycle,
        _attempts,
        readiness,
        queue,
    ) = make_project(
        tmp_path,
        max_attempts=1,
    )

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.INTERRUPTED,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.READY,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.QUEUED,
    )

    with pytest.raises(
        TaskPriorityQueueConflictError,
        match="exhausted attempt budget",
    ):
        queue.ordered_queue(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        record.task_id,
    ).attempt == 1


def test_route_rework_requires_rework_status(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        _tasks,
        record,
        _lifecycle,
        attempts,
        _readiness,
        _queue,
    ) = make_project(tmp_path)

    with pytest.raises(
        TaskAttemptConflictError,
        match="requires REWORK",
    ):
        attempts.route_rework(
            handle.project_id,
            record.task_id,
        )


def test_attempt_survives_restart(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        _tasks,
        record,
        lifecycle,
        _attempts,
        readiness,
        queue,
    ) = make_project(tmp_path)

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )

    restarted_projects = ProjectStore(
        projects.projects_root
    )
    restarted_tasks = TaskStore(restarted_projects)
    restarted_attempts = TaskAttemptStore(
        restarted_projects
    )

    reopened = restarted_tasks.open_task(
        handle.project_id,
        record.task_id,
    )
    budget = restarted_attempts.budget(
        handle.project_id,
        record.task_id,
    )
    assert reopened.attempt == 1
    assert budget.attempt == 1
    assert budget.remaining_attempts == 2


def test_registry_and_project_schema_are_unchanged(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        _tasks,
        record,
        lifecycle,
        _attempts,
        readiness,
        queue,
    ) = make_project(tmp_path)

    queue_and_assign(
        handle,
        record,
        lifecycle,
        readiness,
        queue,
    )
    lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.RUNNING,
    )

    registry = (
        handle.layout.data_directories["tasks"]
        / TASK_REGISTRY_DB_FILENAME
    )
    with sqlite3.connect(registry) as connection:
        task_tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    assert task_tables == (
        "task_dependency_edges",
        "task_registry_meta",
        "tasks",
    )
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_attempt_store_exposes_no_timeout_recovery_or_worker_loop(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    attempts = TaskAttemptStore(projects)

    for name in (
        "timeout",
        "recover_interrupted",
        "cancel_task",
        "assign_next",
        "run_loop",
        "sleep",
    ):
        assert not hasattr(attempts, name)
