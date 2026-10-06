from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    ACTIVE_AGENT_TASK_STATES,
    MAX_PARALLEL_AGENT_TASKS,
    SCHEDULER_LOCK_FILENAME,
    TASK_CONCURRENCY_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCancellationStore,
    TaskConcurrencyLimitError,
    TaskConcurrencyScheduler,
    TaskCreateInput,
    TaskInterruptedRecoveryStore,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
    TaskTransitionConflictError,
)


def task_input(
    title: str,
    *,
    priority: int,
    dependencies=(),
) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=priority,
        dependencies=tuple(dependencies),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def queue_all(
    projects: ProjectStore,
    project_id,
) -> None:
    TaskReadinessResolver(projects).resolve_project(project_id)
    TaskPriorityQueue(projects).enqueue_ready(project_id)


def test_contract_freezes_single_global_slot() -> None:
    assert TASK_CONCURRENCY_CONTRACT_VERSION == "1.0"
    assert MAX_PARALLEL_AGENT_TASKS == 1
    assert ACTIVE_AGENT_TASK_STATES == frozenset(
        {
            TaskState.ASSIGNED,
            TaskState.RUNNING,
        }
    )


def test_assign_next_selects_highest_priority_queued_task(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)

    low = tasks.create_task(
        handle.project_id,
        task_input("Low", priority=10),
    )
    high = tasks.create_task(
        handle.project_id,
        task_input("High", priority=900),
    )
    queue_all(projects, handle.project_id)

    assignment = TaskConcurrencyScheduler(projects).assign_next()

    assert assignment is not None
    assert assignment.task.task_id == high.task_id
    assert assignment.task.status is TaskState.ASSIGNED
    assert assignment.task.attempt == 0
    assert tasks.open_task(
        handle.project_id,
        low.task_id,
    ).status is TaskState.QUEUED


def test_same_priority_uses_project_then_task_id_tiebreaker(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first_project = projects.create_project()
    second_project = projects.create_project()
    tasks = TaskStore(projects)

    first = tasks.create_task(
        first_project.project_id,
        task_input("First", priority=100),
    )
    tasks.create_task(
        second_project.project_id,
        task_input("Second", priority=100),
    )
    queue_all(projects, first_project.project_id)
    queue_all(projects, second_project.project_id)

    assignment = TaskConcurrencyScheduler(projects).assign_next()

    assert assignment is not None
    assert assignment.project_id == first_project.project_id
    assert assignment.task.task_id == first.task_id


def test_assigned_task_consumes_global_slot_across_projects(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first_project = projects.create_project()
    second_project = projects.create_project()
    tasks = TaskStore(projects)

    first = tasks.create_task(
        first_project.project_id,
        task_input("First", priority=10),
    )
    second = tasks.create_task(
        second_project.project_id,
        task_input("Second", priority=900),
    )
    queue_all(projects, first_project.project_id)
    queue_all(projects, second_project.project_id)

    scheduler = TaskConcurrencyScheduler(projects)
    chosen = scheduler.assign_specific(
        first_project.project_id,
        first.task_id,
    )
    assert chosen.task.status is TaskState.ASSIGNED

    assert scheduler.assign_next() is None
    with pytest.raises(TaskConcurrencyLimitError):
        scheduler.assign_specific(
            second_project.project_id,
            second.task_id,
        )

    snapshot = scheduler.snapshot()
    assert snapshot.active_count == 1
    assert snapshot.available_slots == 0


def test_running_task_continues_to_consume_slot(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    first = tasks.create_task(
        handle.project_id,
        task_input("First", priority=100),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input("Second", priority=90),
    )
    queue_all(projects, handle.project_id)

    scheduler = TaskConcurrencyScheduler(projects)
    scheduler.assign_specific(handle.project_id, first.task_id)
    TaskLifecycleStore(projects).transition_task(
        handle.project_id,
        first.task_id,
        TaskState.RUNNING,
        now_epoch_s=1000.0,
    )

    assert scheduler.assign_next() is None
    snapshot = scheduler.snapshot()
    assert snapshot.active_tasks[0].status is TaskState.RUNNING
    assert tasks.open_task(
        handle.project_id,
        second.task_id,
    ).status is TaskState.QUEUED


def test_result_proposed_releases_agent_execution_slot(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    first = tasks.create_task(
        handle.project_id,
        task_input("First", priority=100),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input("Second", priority=90),
    )
    queue_all(projects, handle.project_id)

    scheduler = TaskConcurrencyScheduler(projects)
    scheduler.assign_specific(handle.project_id, first.task_id)
    lifecycle = TaskLifecycleStore(projects)
    lifecycle.transition_task(
        handle.project_id,
        first.task_id,
        TaskState.RUNNING,
        now_epoch_s=1000.0,
    )
    lifecycle.transition_task(
        handle.project_id,
        first.task_id,
        TaskState.RESULT_PROPOSED,
    )

    assignment = scheduler.assign_next()
    assert assignment is not None
    assert assignment.task.task_id == second.task_id
    assert scheduler.snapshot().active_count == 1


def test_generic_lifecycle_assignment_cannot_bypass_global_limit(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first_project = projects.create_project()
    second_project = projects.create_project()
    tasks = TaskStore(projects)
    first = tasks.create_task(
        first_project.project_id,
        task_input("First", priority=100),
    )
    second = tasks.create_task(
        second_project.project_id,
        task_input("Second", priority=100),
    )
    queue_all(projects, first_project.project_id)
    queue_all(projects, second_project.project_id)

    lifecycle = TaskLifecycleStore(projects)
    lifecycle.transition_task(
        first_project.project_id,
        first.task_id,
        TaskState.ASSIGNED,
    )

    with pytest.raises(
        TaskTransitionConflictError,
        match="concurrency gate",
    ):
        lifecycle.transition_task(
            second_project.project_id,
            second.task_id,
            TaskState.ASSIGNED,
        )


def test_cancellation_releases_slot(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    first = tasks.create_task(
        handle.project_id,
        task_input("First", priority=100),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input("Second", priority=90),
    )
    queue_all(projects, handle.project_id)

    scheduler = TaskConcurrencyScheduler(projects)
    scheduler.assign_specific(handle.project_id, first.task_id)
    TaskCancellationStore(projects).cancel_task(
        handle.project_id,
        first.task_id,
    )

    assignment = scheduler.assign_next()
    assert assignment is not None
    assert assignment.task.task_id == second.task_id


def test_interrupted_recovery_releases_running_slot(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    first = tasks.create_task(
        handle.project_id,
        task_input("First", priority=100),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input("Second", priority=90),
    )
    queue_all(projects, handle.project_id)

    scheduler = TaskConcurrencyScheduler(projects)
    scheduler.assign_specific(handle.project_id, first.task_id)
    lifecycle = TaskLifecycleStore(projects)
    lifecycle.transition_task(
        handle.project_id,
        first.task_id,
        TaskState.RUNNING,
        now_epoch_s=1000.0,
    )

    restarted_projects = ProjectStore(projects.projects_root)
    TaskInterruptedRecoveryStore(restarted_projects).recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    assignment = TaskConcurrencyScheduler(
        restarted_projects
    ).assign_next()
    assert assignment is not None
    assert assignment.task.task_id == second.task_id


def test_restart_preserves_assigned_slot_occupancy(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    first = tasks.create_task(
        handle.project_id,
        task_input("First", priority=100),
    )
    tasks.create_task(
        handle.project_id,
        task_input("Second", priority=90),
    )
    queue_all(projects, handle.project_id)

    TaskConcurrencyScheduler(projects).assign_specific(
        handle.project_id,
        first.task_id,
    )

    restarted = TaskConcurrencyScheduler(
        ProjectStore(projects.projects_root)
    )
    snapshot = restarted.snapshot()

    assert snapshot.active_count == 1
    assert snapshot.active_tasks[0].task_id == first.task_id
    assert restarted.assign_next() is None


def test_assignment_never_increments_attempt(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(
        handle.project_id,
        task_input("Task", priority=100),
    )
    queue_all(projects, handle.project_id)

    assigned = TaskConcurrencyScheduler(projects).assign_next()

    assert assigned is not None
    assert assigned.task.task_id == task.task_id
    assert assigned.task.attempt == 0


def test_lock_symlink_fails_closed(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    projects.create_project()
    lock = projects.projects_root.parent / SCHEDULER_LOCK_FILENAME
    external = tmp_path / "external.lock"
    external.write_text("", encoding="utf-8")
    lock.symlink_to(external)

    with pytest.raises(Exception, match="symbolic link"):
        TaskConcurrencyScheduler(projects).snapshot()


def test_registry_and_project_schema_are_unchanged(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    TaskStore(projects).create_task(
        handle.project_id,
        task_input("Task", priority=100),
    )
    queue_all(projects, handle.project_id)
    TaskConcurrencyScheduler(projects).assign_next()

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


def test_concurrency_scheduler_exposes_no_agent_or_model_execution_api(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    scheduler = TaskConcurrencyScheduler(projects)

    for name in (
        "run_agent",
        "invoke_model",
        "generate",
        "spawn_worker",
        "start_px4",
        "mavsdk",
        "sleep",
    ):
        assert not hasattr(scheduler, name)
