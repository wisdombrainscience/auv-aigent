from __future__ import annotations

from pathlib import Path

import pytest

from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    READINESS_MANAGED_STATES,
    TASK_READINESS_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskReadinessConflictError,
    TaskReadinessResolver,
    TaskStore,
)

import sqlite3


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    tasks = TaskStore(projects)
    lifecycle = TaskLifecycleStore(projects)
    readiness = TaskReadinessResolver(projects)
    return projects, handle, tasks, lifecycle, readiness


def task_input(
    title: str,
    dependencies=(),
) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=100,
        dependencies=tuple(dependencies),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def complete_task(
    lifecycle: TaskLifecycleStore,
    project_id,
    task_id,
) -> None:
    current = lifecycle._tasks.open_task(project_id, task_id)
    if current.status is TaskState.CREATED:
        lifecycle.transition_task(
            project_id,
            task_id,
            TaskState.READY,
        )
    elif current.status is TaskState.BLOCKED:
        lifecycle.transition_task(
            project_id,
            task_id,
            TaskState.READY,
        )
    elif current.status is not TaskState.READY:
        raise AssertionError(
            f"test helper expected CREATED/BLOCKED/READY, got {current.status.value}"
        )

    for target in (
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.VALIDATING,
        TaskState.DONE,
    ):
        lifecycle.transition_task(
            project_id,
            task_id,
            target,
        )


def test_contract_and_managed_states_are_explicit() -> None:
    assert TASK_READINESS_CONTRACT_VERSION == "1.0"
    assert READINESS_MANAGED_STATES == frozenset(
        {
            TaskState.CREATED,
            TaskState.BLOCKED,
            TaskState.READY,
        }
    )


def test_empty_project_is_noop(tmp_path: Path) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()

    report = TaskReadinessResolver(projects).resolve_project(
        handle.project_id
    )

    assert report.task_count == 0
    assert report.managed_count == 0
    assert report.changed_count == 0


def test_root_created_task_becomes_ready(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    root = tasks.create_task(
        handle.project_id,
        task_input("Root"),
    )

    report = readiness.resolve_project(handle.project_id)
    reopened = tasks.open_task(handle.project_id, root.task_id)

    assert reopened.status is TaskState.READY
    assert report.ready_count == 1
    assert report.blocked_count == 0
    assert report.changed_count == 1


def test_child_is_blocked_until_direct_dependency_is_done(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, lifecycle, readiness = make_project(
        tmp_path
    )
    root = tasks.create_task(
        handle.project_id,
        task_input("Root"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )

    first = readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        root.task_id,
    ).status is TaskState.READY
    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED
    assert first.ready_count == 1
    assert first.blocked_count == 1

    lifecycle.transition_task(
        handle.project_id,
        root.task_id,
        TaskState.QUEUED,
    )
    lifecycle.transition_task(
        handle.project_id,
        root.task_id,
        TaskState.ASSIGNED,
    )
    lifecycle.transition_task(
        handle.project_id,
        root.task_id,
        TaskState.RUNNING,
    )
    lifecycle.transition_task(
        handle.project_id,
        root.task_id,
        TaskState.RESULT_PROPOSED,
    )
    lifecycle.transition_task(
        handle.project_id,
        root.task_id,
        TaskState.VALIDATING,
    )
    lifecycle.transition_task(
        handle.project_id,
        root.task_id,
        TaskState.DONE,
    )

    second = readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.READY
    assert second.ready_count == 1
    assert second.blocked_count == 0


def test_all_direct_dependencies_must_be_done(tmp_path: Path) -> None:
    _projects, handle, tasks, lifecycle, readiness = make_project(
        tmp_path
    )
    first = tasks.create_task(
        handle.project_id,
        task_input("First"),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input("Second"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input(
            "Child",
            (first.task_id, second.task_id),
        ),
    )

    readiness.resolve_project(handle.project_id)
    complete_task(
        lifecycle,
        handle.project_id,
        first.task_id,
    )
    readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED

    complete_task(
        lifecycle,
        handle.project_id,
        second.task_id,
    )
    readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.READY


def test_cancelled_dependency_does_not_satisfy_readiness(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, lifecycle, readiness = make_project(
        tmp_path
    )
    parent = tasks.create_task(
        handle.project_id,
        task_input("Parent"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (parent.task_id,)),
    )

    lifecycle.transition_task(
        handle.project_id,
        parent.task_id,
        TaskState.CANCELLED,
    )
    readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED


def test_failed_dependency_does_not_satisfy_readiness(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, lifecycle, readiness = make_project(
        tmp_path
    )
    parent = tasks.create_task(
        handle.project_id,
        task_input("Parent"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (parent.task_id,)),
    )

    readiness.resolve_project(handle.project_id)
    lifecycle.transition_task(
        handle.project_id,
        parent.task_id,
        TaskState.QUEUED,
    )
    lifecycle.transition_task(
        handle.project_id,
        parent.task_id,
        TaskState.ASSIGNED,
    )
    lifecycle.transition_task(
        handle.project_id,
        parent.task_id,
        TaskState.RUNNING,
    )
    lifecycle.transition_task(
        handle.project_id,
        parent.task_id,
        TaskState.FAILED,
    )
    readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED


def test_manually_ready_task_with_unsatisfied_dependency_is_corrected_to_blocked(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, lifecycle, readiness = make_project(
        tmp_path
    )
    parent = tasks.create_task(
        handle.project_id,
        task_input("Parent"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (parent.task_id,)),
    )

    lifecycle.transition_task(
        handle.project_id,
        child.task_id,
        TaskState.READY,
    )

    report = readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED
    child_decision = next(
        item
        for item in report.decisions
        if item.task_id == child.task_id
    )
    assert child_decision.previous_status is TaskState.READY
    assert child_decision.resolved_status is TaskState.BLOCKED
    assert child_decision.changed is True


def test_second_resolution_is_idempotent(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    tasks.create_task(
        handle.project_id,
        task_input("Root"),
    )

    first = readiness.resolve_project(handle.project_id)
    second = readiness.resolve_project(handle.project_id)

    assert first.changed_count == 1
    assert second.changed_count == 0
    assert second.ready_count == 1


def test_non_readiness_states_are_not_overwritten(tmp_path: Path) -> None:
    _projects, handle, tasks, lifecycle, readiness = make_project(
        tmp_path
    )
    task = tasks.create_task(
        handle.project_id,
        task_input("Running"),
    )
    readiness.resolve_project(handle.project_id)
    lifecycle.transition_task(
        handle.project_id,
        task.task_id,
        TaskState.QUEUED,
    )
    lifecycle.transition_task(
        handle.project_id,
        task.task_id,
        TaskState.ASSIGNED,
    )
    lifecycle.transition_task(
        handle.project_id,
        task.task_id,
        TaskState.RUNNING,
    )

    report = readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.RUNNING
    assert report.managed_count == 0
    assert report.changed_count == 0


def test_cycle_fails_before_any_status_mutation(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    task = tasks.create_task(
        handle.project_id,
        task_input("Self", ("TASK-000001",)),
    )

    with pytest.raises(
        TaskReadinessConflictError,
        match="not readiness-resolvable",
    ):
        readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.CREATED


def test_missing_dependency_fails_before_any_status_mutation(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    task = tasks.create_task(
        handle.project_id,
        task_input("Missing", ("TASK-999999",)),
    )

    with pytest.raises(
        TaskReadinessConflictError,
        match="not readiness-resolvable",
    ):
        readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.CREATED


def test_restart_preserves_resolved_statuses(tmp_path: Path) -> None:
    projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    root = tasks.create_task(
        handle.project_id,
        task_input("Root"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )

    readiness.resolve_project(handle.project_id)

    restarted_projects = ProjectStore(projects.projects_root)
    restarted_tasks = TaskStore(restarted_projects)
    restarted_readiness = TaskReadinessResolver(
        restarted_projects
    )
    report = restarted_readiness.resolve_project(
        handle.project_id
    )

    assert restarted_tasks.open_task(
        handle.project_id,
        root.task_id,
    ).status is TaskState.READY
    assert restarted_tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED
    assert report.changed_count == 0


def test_readiness_changes_status_only_attempt_remains_zero(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    root = tasks.create_task(
        handle.project_id,
        task_input("Root"),
    )

    readiness.resolve_project(handle.project_id)
    reopened = tasks.open_task(handle.project_id, root.task_id)

    assert reopened.attempt == 0
    assert reopened.status is TaskState.READY


def test_registry_and_project_schema_are_unchanged(tmp_path: Path) -> None:
    projects, handle, tasks, _lifecycle, readiness = make_project(
        tmp_path
    )
    tasks.create_task(
        handle.project_id,
        task_input("Root"),
    )
    readiness.resolve_project(handle.project_id)

    registry = (
        handle.layout.data_directories["tasks"]
        / TASK_REGISTRY_DB_FILENAME
    )
    with sqlite3.connect(registry) as connection:
        task_tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
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
                SELECT name
                FROM sqlite_master
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


def test_t5_5_exposes_no_queue_attempt_timeout_or_execution_api(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    resolver = TaskReadinessResolver(projects)

    for name in (
        "enqueue",
        "dequeue",
        "next_task",
        "increment_attempt",
        "timeout",
        "recover_interrupted",
        "cancel_task",
        "run",
        "assign",
    ):
        assert not hasattr(resolver, name)
