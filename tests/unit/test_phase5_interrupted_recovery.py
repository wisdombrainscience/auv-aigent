from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

import ai.tasks.recovery as recovery_module
from ai.contracts.failures import FailureClass
from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    MAX_INTERRUPTED_RECOVERIES_PER_SCAN,
    TASK_INTERRUPTED_RECOVERY_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskInterruptedRecoveryConflictError,
    TaskInterruptedRecoveryStore,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
)


def task_input(
    title: str,
    *,
    dependencies=(),
    max_attempts: int = 3,
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
        max_attempts=max_attempts,
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
    recovery = TaskInterruptedRecoveryStore(projects)
    return (
        projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    )


def enter_running(
    handle,
    task_id,
    lifecycle,
    readiness,
    queue,
    *,
    now_epoch_s: float = 1000.0,
):
    readiness.resolve_project(handle.project_id)
    queue.enqueue_ready(handle.project_id)
    lifecycle.transition_task(
        handle.project_id,
        task_id,
        TaskState.ASSIGNED,
    )
    return lifecycle.transition_task(
        handle.project_id,
        task_id,
        TaskState.RUNNING,
        now_epoch_s=now_epoch_s,
    )


def test_contract_and_scan_bound_are_explicit() -> None:
    assert TASK_INTERRUPTED_RECOVERY_CONTRACT_VERSION == "1.0"
    assert MAX_INTERRUPTED_RECOVERIES_PER_SCAN == 4096


def test_project_without_running_tasks_is_noop(tmp_path: Path) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Created"),
    )

    report = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1200.0,
    )

    assert report.recovered_count == 0
    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.CREATED


def test_running_task_recovers_to_interrupted_and_preserves_attempt(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    running = enter_running(
        handle,
        task.task_id,
        TaskLifecycleStore(recovery._tasks._project_store),
        readiness,
        queue,
        now_epoch_s=1000.0,
    )

    report = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )
    reopened = tasks.open_task(
        handle.project_id,
        task.task_id,
    )

    assert running.attempt == 1
    assert reopened.status is TaskState.INTERRUPTED
    assert reopened.attempt == 1
    assert report.recovered_count == 1
    assert report.decisions[0].attempt == 1
    assert report.decisions[0].deadline_passed is False


def test_recovery_clears_t5_8_execution_timing_metadata(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
        now_epoch_s=1000.0,
    )
    recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    registry = (
        handle.layout.data_directories["tasks"]
        / TASK_REGISTRY_DB_FILENAME
    )
    with sqlite3.connect(registry) as connection:
        row = connection.execute(
            """
            SELECT execution_attempt,
                   started_at_epoch_s,
                   deadline_at_epoch_s
            FROM tasks
            WHERE task_id=?
            """,
            (task.task_id.value,),
        ).fetchone()

    assert row == (None, None, None)


def test_recovery_failure_is_infra_task_interrupted(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
        now_epoch_s=1000.0,
    )

    decision = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    ).decisions[0]

    assert decision.failure.failure_class is FailureClass.INFRA
    assert decision.failure.code == "INFRA_TASK_INTERRUPTED"
    assert decision.failure.details["task_id"] == task.task_id.value
    assert decision.failure.details["attempt"] == 1


def test_deadline_passed_is_reported_but_state_is_still_interrupted(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
        now_epoch_s=1000.0,
    )

    decision = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1400.0,
    ).decisions[0]

    assert decision.deadline_passed is True
    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.INTERRUPTED


def test_non_running_states_are_untouched(tmp_path: Path) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)

    queued = tasks.create_task(
        handle.project_id,
        task_input("Queued"),
    )
    assigned = tasks.create_task(
        handle.project_id,
        task_input("Assigned"),
    )
    done = tasks.create_task(
        handle.project_id,
        task_input("Done"),
    )

    readiness.resolve_project(handle.project_id)
    queue.enqueue_ready(handle.project_id)

    # Complete DONE first, releasing the single execution slot, then leave one
    # separate task ASSIGNED. QUEUED/ASSIGNED/DONE are all non-RUNNING and must
    # remain untouched by T5.9 recovery.
    lifecycle.transition_task(
        handle.project_id,
        done.task_id,
        TaskState.ASSIGNED,
    )
    lifecycle.transition_task(
        handle.project_id,
        done.task_id,
        TaskState.RUNNING,
        now_epoch_s=1000.0,
    )
    lifecycle.transition_task(
        handle.project_id,
        done.task_id,
        TaskState.RESULT_PROPOSED,
    )
    lifecycle.transition_task(
        handle.project_id,
        done.task_id,
        TaskState.VALIDATING,
    )
    lifecycle.transition_task(
        handle.project_id,
        done.task_id,
        TaskState.DONE,
    )
    lifecycle.transition_task(
        handle.project_id,
        assigned.task_id,
        TaskState.ASSIGNED,
    )

    report = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    assert report.recovered_count == 0
    assert tasks.open_task(
        handle.project_id,
        queued.task_id,
    ).status is TaskState.QUEUED
    assert tasks.open_task(
        handle.project_id,
        assigned.task_id,
    ).status is TaskState.ASSIGNED
    assert tasks.open_task(
        handle.project_id,
        done.task_id,
    ).status is TaskState.DONE


def test_single_running_task_recovery_is_deterministic(
    tmp_path: Path,
) -> None:
    # T5.11 makes more than one simultaneous ASSIGNED/RUNNING task impossible
    # through supported APIs. T5.9 therefore only needs to recover the single
    # globally active RUNNING slot deterministically.
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)

    task = tasks.create_task(
        handle.project_id,
        task_input("Running"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
        now_epoch_s=1000.0,
    )

    report = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    assert tuple(
        item.task_id for item in report.decisions
    ) == (task.task_id,)
    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.INTERRUPTED


def test_recovery_is_idempotent_after_running_tasks_are_recovered(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
    )

    first = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )
    second = recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1200.0,
    )

    assert first.recovered_count == 1
    assert second.recovered_count == 0


def test_interrupted_with_remaining_budget_routes_to_blocked(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run", max_attempts=3),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
    )
    recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    route = recovery.route_interrupted(
        handle.project_id,
        task.task_id,
    )

    assert route.attempt == 1
    assert route.max_attempts == 3
    assert route.target_status is TaskState.BLOCKED
    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).attempt == 1


def test_interrupted_exhausted_budget_routes_to_needs_human(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run", max_attempts=1),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
    )
    recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    route = recovery.route_interrupted(
        handle.project_id,
        task.task_id,
    )

    assert route.target_status is TaskState.NEEDS_HUMAN
    reopened = tasks.open_task(
        handle.project_id,
        task.task_id,
    )
    assert reopened.status is TaskState.NEEDS_HUMAN
    assert reopened.attempt == 1


def test_route_interrupted_requires_interrupted_state(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        _lifecycle,
        _readiness,
        _queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Created"),
    )

    with pytest.raises(
        TaskInterruptedRecoveryConflictError,
        match="requires INTERRUPTED",
    ):
        recovery.route_interrupted(
            handle.project_id,
            task.task_id,
        )


def test_blocked_retry_reenters_t5_5_without_consuming_attempt(
    tmp_path: Path,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
    )
    recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )
    recovery.route_interrupted(
        handle.project_id,
        task.task_id,
    )

    readiness.resolve_project(handle.project_id)
    reopened = tasks.open_task(
        handle.project_id,
        task.task_id,
    )

    assert reopened.status is TaskState.READY
    assert reopened.attempt == 1


def test_restart_sees_persisted_interrupted_state(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
    )
    recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
    )

    restarted_tasks = TaskStore(
        ProjectStore(projects.projects_root)
    )
    reopened = restarted_tasks.open_task(
        handle.project_id,
        task.task_id,
    )

    assert reopened.status is TaskState.INTERRUPTED
    assert reopened.attempt == 1


def test_recovery_scan_bound_fails_closed_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)

    task = tasks.create_task(
        handle.project_id,
        task_input("Running"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
        now_epoch_s=1000.0,
    )

    # A zero bound forces the one legal RUNNING slot to exceed the scan limit.
    monkeypatch.setattr(
        recovery_module,
        "MAX_INTERRUPTED_RECOVERIES_PER_SCAN",
        0,
    )

    with pytest.raises(
        TaskInterruptedRecoveryConflictError,
        match="MAX_INTERRUPTED_RECOVERIES_PER_SCAN",
    ):
        recovery.recover_project(
            handle.project_id,
            observed_at_epoch_s=1100.0,
        )

    assert tasks.open_task(
        handle.project_id,
        task.task_id,
    ).status is TaskState.RUNNING


def test_registry_and_project_schema_are_unchanged(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        tasks,
        lifecycle,
        readiness,
        queue,
        recovery,
    ) = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input("Run"),
    )
    enter_running(
        handle,
        task.task_id,
        lifecycle,
        readiness,
        queue,
    )
    recovery.recover_project(
        handle.project_id,
        observed_at_epoch_s=1100.0,
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


def test_recovery_store_exposes_no_cancellation_concurrency_or_worker_loop(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    recovery = TaskInterruptedRecoveryStore(projects)

    for name in (
        "cancel_task",
        "cancel_project",
        "set_concurrency",
        "run_loop",
        "sleep",
        "spawn_worker",
    ):
        assert not hasattr(recovery, name)
