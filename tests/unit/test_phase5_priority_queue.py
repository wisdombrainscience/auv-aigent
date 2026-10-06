from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    MAX_PRIORITY_QUEUE_ITEMS,
    PRIORITY_POLICY,
    PRIORITY_TIE_BREAKER,
    TASK_PRIORITY_QUEUE_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskPriorityQueueConflictError,
    TaskReadinessResolver,
    TaskStore,
)


def make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    lifecycle = TaskLifecycleStore(projects)
    queue = TaskPriorityQueue(projects)
    return projects, handle, tasks, lifecycle, queue


def task_input(title: str, *, priority: int, dependencies=()) -> TaskCreateInput:
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


def finish_queued_task(lifecycle, project_id, task_id) -> None:
    for target in (
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.VALIDATING,
        TaskState.DONE,
    ):
        lifecycle.transition_task(project_id, task_id, target)


def test_contract_and_policy_are_explicit() -> None:
    assert TASK_PRIORITY_QUEUE_CONTRACT_VERSION == "1.1"
    assert PRIORITY_POLICY == "HIGHER_NUMERIC_VALUE_FIRST"
    assert PRIORITY_TIE_BREAKER == "CANONICAL_TASK_ID_ASC"
    assert MAX_PRIORITY_QUEUE_ITEMS == 4096


def test_empty_project_has_empty_queue(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    queue = TaskPriorityQueue(projects)

    report = queue.enqueue_ready(handle.project_id)
    assert report.queued_count == 0
    assert report.newly_queued_count == 0
    assert report.items == ()
    assert queue.peek_next(handle.project_id) is None


def test_higher_numeric_priority_is_first(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    low = tasks.create_task(handle.project_id, task_input("Low", priority=10))
    high = tasks.create_task(handle.project_id, task_input("High", priority=900))
    medium = tasks.create_task(handle.project_id, task_input("Medium", priority=100))

    report = queue.enqueue_ready(handle.project_id)

    assert tuple(item.task_id for item in report.items) == (
        high.task_id,
        medium.task_id,
        low.task_id,
    )
    assert tuple(item.priority for item in report.items) == (900, 100, 10)


def test_equal_priority_uses_canonical_task_id_tie_breaker(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    first = tasks.create_task(handle.project_id, task_input("First", priority=100))
    second = tasks.create_task(handle.project_id, task_input("Second", priority=100))
    third = tasks.create_task(handle.project_id, task_input("Third", priority=100))

    items = queue.enqueue_ready(handle.project_id).items
    assert tuple(item.task_id for item in items) == (
        first.task_id,
        second.task_id,
        third.task_id,
    )


def test_enqueue_ready_changes_ready_to_queued_only(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root", priority=10))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", priority=1000, dependencies=(root.task_id,)),
    )

    report = queue.enqueue_ready(handle.project_id)

    assert report.newly_queued_count == 1
    assert tasks.open_task(handle.project_id, root.task_id).status is TaskState.QUEUED
    assert tasks.open_task(handle.project_id, child.task_id).status is TaskState.BLOCKED


def test_dependency_done_then_child_can_enter_queue(tmp_path: Path) -> None:
    _projects, handle, tasks, lifecycle, queue = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root", priority=10))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", priority=500, dependencies=(root.task_id,)),
    )

    queue.enqueue_ready(handle.project_id)
    finish_queued_task(lifecycle, handle.project_id, root.task_id)
    report = queue.enqueue_ready(handle.project_id)

    assert tasks.open_task(handle.project_id, child.task_id).status is TaskState.QUEUED
    assert report.newly_queued_count == 1
    assert report.items[0].task_id == child.task_id


def test_peek_next_is_read_only(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("Top", priority=999))
    queue.enqueue_ready(handle.project_id)

    item = queue.peek_next(handle.project_id)
    assert item is not None
    assert item.task_id == task.task_id
    assert tasks.open_task(handle.project_id, task.task_id).status is TaskState.QUEUED


def test_queue_is_idempotent(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("One", priority=100))

    first = queue.enqueue_ready(handle.project_id)
    second = queue.enqueue_ready(handle.project_id)

    assert first.newly_queued_count == 1
    assert second.newly_queued_count == 0
    assert second.queued_count == 1
    assert second.items[0].task_id == task.task_id


def test_new_higher_priority_task_joins_ahead_of_existing_queue(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    old = tasks.create_task(handle.project_id, task_input("Old", priority=20))
    queue.enqueue_ready(handle.project_id)

    new = tasks.create_task(handle.project_id, task_input("New", priority=500))
    items = queue.enqueue_ready(handle.project_id).items

    assert tuple(item.task_id for item in items) == (new.task_id, old.task_id)


def test_queue_does_not_increment_attempt(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("One", priority=100))
    queue.enqueue_ready(handle.project_id)

    reopened = tasks.open_task(handle.project_id, task.task_id)
    assert reopened.status is TaskState.QUEUED
    assert reopened.attempt == 0


def test_queue_does_not_assign_or_run_tasks(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("One", priority=100))
    queue.enqueue_ready(handle.project_id)

    reopened = tasks.open_task(handle.project_id, task.task_id)
    assert reopened.status is TaskState.QUEUED
    assert not hasattr(queue, "assign_next")
    assert not hasattr(queue, "run_next")
    assert not hasattr(queue, "acquire_slot")


def test_invalid_manually_queued_task_with_unfinished_parent_fails_closed(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, lifecycle, queue = make_project(tmp_path)
    parent = tasks.create_task(handle.project_id, task_input("Parent", priority=10))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", priority=100, dependencies=(parent.task_id,)),
    )

    lifecycle.transition_task(handle.project_id, child.task_id, TaskState.READY)
    lifecycle.transition_task(handle.project_id, child.task_id, TaskState.QUEUED)

    with pytest.raises(
        TaskPriorityQueueConflictError,
        match="unfinished dependency",
    ):
        queue.ordered_queue(handle.project_id)


def test_cycle_fails_before_queue_mutation(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Self", priority=100, dependencies=("TASK-000001",)),
    )

    with pytest.raises(TaskPriorityQueueConflictError):
        queue.enqueue_ready(handle.project_id)

    assert tasks.open_task(handle.project_id, task.task_id).status is TaskState.CREATED


def test_missing_dependency_fails_before_queue_mutation(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Missing", priority=100, dependencies=("TASK-999999",)),
    )

    with pytest.raises(TaskPriorityQueueConflictError):
        queue.enqueue_ready(handle.project_id)

    assert tasks.open_task(handle.project_id, task.task_id).status is TaskState.CREATED


def test_restart_preserves_queue_order(tmp_path: Path) -> None:
    projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    low = tasks.create_task(handle.project_id, task_input("Low", priority=1))
    high = tasks.create_task(handle.project_id, task_input("High", priority=1000))
    queue.enqueue_ready(handle.project_id)

    restarted = TaskPriorityQueue(ProjectStore(projects.projects_root))
    items = restarted.ordered_queue(handle.project_id)
    assert tuple(item.task_id for item in items) == (high.task_id, low.task_id)


def test_task_and_project_schemas_are_unchanged(tmp_path: Path) -> None:
    projects, handle, tasks, _lifecycle, queue = make_project(tmp_path)
    tasks.create_task(handle.project_id, task_input("One", priority=100))
    queue.enqueue_ready(handle.project_id)

    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
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


def test_t5_6_exposes_no_attempt_timeout_recovery_cancellation_or_concurrency_api(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    queue = TaskPriorityQueue(projects)

    for name in (
        "increment_attempt",
        "timeout",
        "recover_interrupted",
        "cancel_task",
        "set_concurrency_limit",
        "spawn_worker",
    ):
        assert not hasattr(queue, name)
