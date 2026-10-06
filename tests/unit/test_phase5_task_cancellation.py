from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

import ai.tasks.cancellation as cancellation_module
from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks.store import _canonical_record_json, _registry_path

from ai.tasks import (
    CANCELLABLE_TASK_STATES,
    MAX_CANCELLATION_TARGETS,
    TASK_CANCELLATION_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCancellationConflictError,
    TaskCancellationStore,
    TaskCreateInput,
    TaskDAGCycleError,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
)


def task_input(title: str, dependencies=()) -> TaskCreateInput:
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


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    tasks = TaskStore(projects)
    lifecycle = TaskLifecycleStore(projects)
    readiness = TaskReadinessResolver(projects)
    queue = TaskPriorityQueue(projects)
    cancellation = TaskCancellationStore(projects)
    return (
        projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        cancellation,
    )


def drive_to_state(
    handle,
    task_id,
    lifecycle: TaskLifecycleStore,
    target: TaskState,
) -> None:
    if target is TaskState.CREATED:
        return
    if target is TaskState.BLOCKED:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.BLOCKED)
        return
    if target is TaskState.READY:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.READY)
        return
    if target is TaskState.CANCELLED:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.CANCELLED)
        return
    if target is TaskState.FAILED:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.BLOCKED)
        lifecycle.transition_task(handle.project_id, task_id, TaskState.FAILED)
        return

    lifecycle.transition_task(handle.project_id, task_id, TaskState.READY)
    if target is TaskState.QUEUED:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.QUEUED)
        return
    lifecycle.transition_task(handle.project_id, task_id, TaskState.QUEUED)
    if target is TaskState.ASSIGNED:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.ASSIGNED)
        return
    lifecycle.transition_task(handle.project_id, task_id, TaskState.ASSIGNED)
    lifecycle.transition_task(
        handle.project_id,
        task_id,
        TaskState.RUNNING,
        now_epoch_s=1000.0,
    )
    if target is TaskState.RUNNING:
        return
    if target is TaskState.INTERRUPTED:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.INTERRUPTED)
        return
    if target is TaskState.NEEDS_HUMAN:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.NEEDS_HUMAN)
        return

    lifecycle.transition_task(
        handle.project_id,
        task_id,
        TaskState.RESULT_PROPOSED,
    )
    if target is TaskState.RESULT_PROPOSED:
        return
    if target is TaskState.REVIEWING:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.REVIEWING)
        return
    if target is TaskState.REWORK:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.REWORK)
        return
    if target is TaskState.VALIDATING:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.VALIDATING)
        return
    if target is TaskState.DONE:
        lifecycle.transition_task(handle.project_id, task_id, TaskState.VALIDATING)
        lifecycle.transition_task(handle.project_id, task_id, TaskState.DONE)
        return
    raise AssertionError(f"unhandled target for test helper: {target}")


def test_contract_and_bound_are_explicit() -> None:
    assert TASK_CANCELLATION_CONTRACT_VERSION == "1.0"
    assert MAX_CANCELLATION_TARGETS == 4096
    assert TaskState.DONE not in CANCELLABLE_TASK_STATES
    assert TaskState.FAILED not in CANCELLABLE_TASK_STATES
    assert TaskState.CANCELLED in CANCELLABLE_TASK_STATES


@pytest.mark.parametrize(
    "initial",
    [
        TaskState.CREATED,
        TaskState.BLOCKED,
        TaskState.READY,
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.REVIEWING,
        TaskState.VALIDATING,
        TaskState.REWORK,
        TaskState.INTERRUPTED,
        TaskState.NEEDS_HUMAN,
    ],
)
def test_explicit_cancellation_supports_every_nonterminal_contract_state(
    tmp_path: Path,
    initial: TaskState,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input(initial.value))
    drive_to_state(handle, task.task_id, lifecycle, initial)
    before = tasks.open_task(handle.project_id, task.task_id)

    report = cancellation.cancel_task(handle.project_id, task.task_id)
    after = tasks.open_task(handle.project_id, task.task_id)

    assert report.targeted_count == 1
    assert report.cancelled_count == 1
    assert after.status is TaskState.CANCELLED
    assert after.attempt == before.attempt


def test_cancelled_is_idempotent(tmp_path: Path) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("Idempotent"))

    first = cancellation.cancel_task(handle.project_id, task.task_id)
    second = cancellation.cancel_task(handle.project_id, task.task_id)

    assert first.cancelled_count == 1
    assert second.cancelled_count == 0
    assert second.already_cancelled_count == 1
    assert second.decisions[0].changed is False


@pytest.mark.parametrize("terminal", [TaskState.DONE, TaskState.FAILED])
def test_done_and_failed_root_outcomes_cannot_be_rewritten(
    tmp_path: Path,
    terminal: TaskState,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input(terminal.value))
    drive_to_state(handle, task.task_id, lifecycle, terminal)

    with pytest.raises(
        TaskCancellationConflictError,
        match="cannot be cancelled",
    ):
        cancellation.cancel_task(handle.project_id, task.task_id)

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is terminal


def test_running_cancellation_clears_timeout_metadata_and_preserves_attempt(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("Running"))
    drive_to_state(handle, task.task_id, lifecycle, TaskState.RUNNING)

    cancellation.cancel_task(handle.project_id, task.task_id)

    reopened = tasks.open_task(handle.project_id, task.task_id)
    assert reopened.status is TaskState.CANCELLED
    assert reopened.attempt == 1

    registry = (
        handle.layout.data_directories["tasks"]
        / TASK_REGISTRY_DB_FILENAME
    )
    with sqlite3.connect(registry) as connection:
        timing = connection.execute(
            """
            SELECT execution_attempt,
                   started_at_epoch_s,
                   deadline_at_epoch_s
            FROM tasks
            WHERE task_id=?
            """,
            (task.task_id.value,),
        ).fetchone()
    assert timing == (None, None, None)


def test_cascade_cancels_transitive_dependents_but_not_unrelated(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )
    leaf = tasks.create_task(
        handle.project_id,
        task_input("Leaf", (child.task_id,)),
    )
    unrelated = tasks.create_task(
        handle.project_id,
        task_input("Unrelated"),
    )
    readiness.resolve_project(handle.project_id)

    report = cancellation.cancel_task(
        handle.project_id,
        root.task_id,
        cascade_dependents=True,
    )

    assert report.targeted_count == 3
    assert report.cancelled_count == 3
    assert tuple(item.task_id for item in report.decisions) == (
        root.task_id,
        child.task_id,
        leaf.task_id,
    )
    for task in (root, child, leaf):
        assert tasks.open_task(
            handle.project_id,
            task.task_id,
        ).status is TaskState.CANCELLED
    assert tasks.open_task(
        handle.project_id,
        unrelated.task_id,
    ).status is TaskState.READY


def test_exact_cancellation_does_not_implicitly_cancel_dependents(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )
    readiness.resolve_project(handle.project_id)

    report = cancellation.cancel_task(handle.project_id, root.task_id)

    assert report.targeted_count == 1
    assert tasks.open_task(
        handle.project_id,
        root.task_id,
    ).status is TaskState.CANCELLED
    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED


def test_cancelled_dependency_never_satisfies_t5_5_readiness(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )
    readiness.resolve_project(handle.project_id)
    cancellation.cancel_task(handle.project_id, root.task_id)
    readiness.resolve_project(handle.project_id)

    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.BLOCKED


def test_cascade_from_already_cancelled_root_can_finish_descendants(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )
    readiness.resolve_project(handle.project_id)
    cancellation.cancel_task(handle.project_id, root.task_id)

    report = cancellation.cancel_task(
        handle.project_id,
        root.task_id,
        cascade_dependents=True,
    )

    assert report.targeted_count == 2
    assert report.already_cancelled_count == 1
    assert report.cancelled_count == 1
    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.CANCELLED


def test_cascade_skips_historical_terminal_descendant_without_rewrite(
    tmp_path: Path,
) -> None:
    # T5.11 correctly prevents creating this inconsistent history through the
    # lifecycle API. Reconstruct it directly as a schema-valid persisted legacy
    # fixture to verify cancellation still never rewrites terminal DONE history.
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )

    registry = _registry_path(handle)
    with sqlite3.connect(registry) as connection:
        row = connection.execute(
            "SELECT record_json FROM tasks WHERE task_id=?",
            (child.task_id.value,),
        ).fetchone()
        current = tasks.open_task(handle.project_id, child.task_id)
        historical = replace(current, status=TaskState.DONE)
        connection.execute(
            """
            UPDATE tasks
            SET status=?, record_json=?
            WHERE task_id=? AND record_json=?
            """,
            (
                historical.status.value,
                _canonical_record_json(historical),
                child.task_id.value,
                row[0],
            ),
        )
        connection.commit()

    report = cancellation.cancel_task(
        handle.project_id,
        root.task_id,
        cascade_dependents=True,
    )

    assert report.terminal_skipped_count == 1
    assert tasks.open_task(
        handle.project_id,
        root.task_id,
    ).status is TaskState.CANCELLED
    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.DONE


def test_cascade_requires_valid_dag_and_fails_before_mutation(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Self", ("TASK-000001",)),
    )

    with pytest.raises(
        TaskCancellationConflictError,
        match="requires valid task DAG",
    ):
        cancellation.cancel_task(
            handle.project_id,
            task.task_id,
            cascade_dependents=True,
        )

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.CREATED


def test_exact_cancellation_still_works_as_cycle_escape_hatch(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Self", ("TASK-000001",)),
    )

    cancellation.cancel_task(handle.project_id, task.task_id)

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.CANCELLED


def test_cascade_bound_fails_closed_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input("Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input("Child", (root.task_id,)),
    )
    monkeypatch.setattr(cancellation_module, "MAX_CANCELLATION_TARGETS", 1)

    with pytest.raises(
        TaskCancellationConflictError,
        match="MAX_CANCELLATION_TARGETS",
    ):
        cancellation.cancel_task(
            handle.project_id,
            root.task_id,
            cascade_dependents=True,
        )

    assert tasks.open_task(
        handle.project_id,
        root.task_id,
    ).status is TaskState.CREATED
    assert tasks.open_task(
        handle.project_id,
        child.task_id,
    ).status is TaskState.CREATED


def test_cancelled_queued_task_disappears_from_priority_queue(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        readiness,
        queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("Queued"))
    readiness.resolve_project(handle.project_id)
    queue.enqueue_ready(handle.project_id)
    assert queue.peek_next(handle.project_id).task_id == task.task_id

    cancellation.cancel_task(handle.project_id, task.task_id)

    assert queue.peek_next(handle.project_id) is None


def test_cancellation_survives_restart(tmp_path: Path) -> None:
    (
        projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("Restart"))
    cancellation.cancel_task(handle.project_id, task.task_id)

    restarted_tasks = TaskStore(ProjectStore(projects.projects_root))
    reopened = restarted_tasks.open_task(handle.project_id, task.task_id)

    assert reopened.status is TaskState.CANCELLED
    assert reopened.attempt == 0


def test_registry_and_project_schema_are_unchanged(tmp_path: Path) -> None:
    (
        projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        cancellation,
    ) = make_project(tmp_path)
    task = tasks.create_task(handle.project_id, task_input("Schema"))
    cancellation.cancel_task(handle.project_id, task.task_id)

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


def test_t5_10_exposes_no_concurrency_worker_or_agent_execution_api(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    cancellation = TaskCancellationStore(projects)

    for name in (
        "set_concurrency",
        "acquire_slot",
        "release_slot",
        "spawn_worker",
        "kill_process",
        "run_agent",
        "run_loop",
    ):
        assert not hasattr(cancellation, name)
