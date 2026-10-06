from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.contracts.failures import FailureClass
from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    TASK_REGISTRY_DB_FILENAME,
    TASK_REGISTRY_SCHEMA_VERSION,
    TASK_TIMEOUT_CONTRACT_VERSION,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskReadinessResolver,
    TaskPriorityQueue,
    TaskStore,
    TaskTimeoutConflictError,
    TaskTimeoutStore,
)
from ai.tasks.lifecycle import TASK_TIMEOUT_S


def make_running(tmp_path: Path, *, start: float = 1000.0):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Timed engineering task",
            role="uav_systems_engineer",
            priority=100,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema",),
            review_required=False,
            max_attempts=3,
        ),
    )
    lifecycle = TaskLifecycleStore(projects)
    TaskReadinessResolver(projects).resolve_project(handle.project_id)
    TaskPriorityQueue(projects).enqueue_ready(handle.project_id)
    lifecycle.transition_task(handle.project_id, task.task_id, TaskState.ASSIGNED)
    running = lifecycle.transition_task(
        handle.project_id,
        task.task_id,
        TaskState.RUNNING,
        now_epoch_s=start,
    )
    return projects, handle, tasks, lifecycle, TaskTimeoutStore(projects), running


def test_contract_and_frozen_timeout_are_explicit() -> None:
    assert TASK_TIMEOUT_CONTRACT_VERSION == "1.0"
    assert TASK_TIMEOUT_S == 300.0
    assert TASK_REGISTRY_SCHEMA_VERSION == 3


def test_running_transition_persists_deadline(tmp_path: Path) -> None:
    _projects, handle, _tasks, _lifecycle, timeouts, running = make_running(tmp_path)
    window = timeouts.window(handle.project_id, running.task_id)
    assert window.attempt == 1
    assert window.started_at_epoch_s == 1000.0
    assert window.deadline_at_epoch_s == 1300.0
    assert window.timeout_s == 300.0


def test_before_deadline_does_not_expire(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, timeouts, running = make_running(tmp_path)
    report = timeouts.expire_overdue(handle.project_id, now_epoch_s=1299.999)
    assert report.expired_count == 0
    assert tasks.open_task(handle.project_id, running.task_id).status is TaskState.RUNNING


def test_at_deadline_transitions_to_needs_human(tmp_path: Path) -> None:
    _projects, handle, tasks, _lifecycle, timeouts, running = make_running(tmp_path)
    report = timeouts.expire_overdue(handle.project_id, now_epoch_s=1300.0)
    assert report.expired_count == 1
    decision = report.decisions[0]
    assert decision.target_status is TaskState.NEEDS_HUMAN
    assert decision.attempt == 1
    assert decision.failure.failure_class is FailureClass.TASK
    assert decision.failure.code == "TASK_TIMEOUT"
    reopened = tasks.open_task(handle.project_id, running.task_id)
    assert reopened.status is TaskState.NEEDS_HUMAN
    assert reopened.attempt == 1
    with pytest.raises(TaskTimeoutConflictError, match="RUNNING"):
        timeouts.window(handle.project_id, running.task_id)


def test_normal_running_exit_clears_timeout_metadata(tmp_path: Path) -> None:
    _projects, handle, _tasks, lifecycle, timeouts, running = make_running(tmp_path)
    lifecycle.transition_task(
        handle.project_id,
        running.task_id,
        TaskState.RESULT_PROPOSED,
    )
    with pytest.raises(TaskTimeoutConflictError, match="RUNNING"):
        timeouts.window(handle.project_id, running.task_id)


def test_restart_preserves_active_deadline_and_can_expire(tmp_path: Path) -> None:
    projects, handle, _tasks, _lifecycle, _timeouts, running = make_running(tmp_path)
    restarted_projects = ProjectStore(projects.projects_root)
    restarted_timeouts = TaskTimeoutStore(restarted_projects)
    window = restarted_timeouts.window(handle.project_id, running.task_id)
    assert window.deadline_at_epoch_s == 1300.0
    report = restarted_timeouts.expire_overdue(handle.project_id, now_epoch_s=1301.0)
    assert report.expired_count == 1
    assert TaskStore(restarted_projects).open_task(
        handle.project_id, running.task_id
    ).status is TaskState.NEEDS_HUMAN


def test_remaining_seconds_is_bounded_at_zero(tmp_path: Path) -> None:
    _projects, handle, _tasks, _lifecycle, timeouts, running = make_running(tmp_path)
    assert timeouts.remaining_seconds(
        handle.project_id, running.task_id, now_epoch_s=1100.0
    ) == 200.0
    assert timeouts.remaining_seconds(
        handle.project_id, running.task_id, now_epoch_s=1400.0
    ) == 0.0


def test_invalid_clock_value_fails_closed(tmp_path: Path) -> None:
    _projects, handle, _tasks, _lifecycle, timeouts, _running = make_running(tmp_path)
    with pytest.raises(TaskTimeoutConflictError):
        timeouts.expire_overdue(handle.project_id, now_epoch_s=float("nan"))


def test_registry_schema_stays_three_tables_with_runtime_columns(tmp_path: Path) -> None:
    projects, handle, _tasks, _lifecycle, _timeouts, _running = make_running(tmp_path)
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        tables = tuple(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        )
    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert tables == ("task_dependency_edges", "task_registry_meta", "tasks")
    assert {"execution_attempt", "started_at_epoch_s", "deadline_at_epoch_s"} <= set(columns)
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_t5_8_has_no_retry_sleep_or_interrupted_recovery_api(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    timeouts = TaskTimeoutStore(projects)
    for name in (
        "retry", "sleep", "recover_interrupted", "cancel_task",
        "assign_next", "run_loop",
    ):
        assert not hasattr(timeouts, name)


def _write_t5_7_v2_registry(registry: Path, *, project_id: str, status: str, attempt: int) -> None:
    import json
    record = {
        "task_id": "TASK-000001",
        "project_id": project_id,
        "title": "Legacy T5.7 task",
        "role": "uav_systems_engineer",
        "priority": 100,
        "dependencies": [],
        "input_artifacts": [],
        "output_schema": "engineering_task.v1",
        "validators": ["schema"],
        "review_required": False,
        "attempt": attempt,
        "max_attempts": 3,
        "status": status,
    }
    registry.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(registry) as connection:
        connection.execute(
            """
            CREATE TABLE task_registry_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 2)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE task_dependency_edges (
                child_task_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                dependency_task_id TEXT NOT NULL,
                PRIMARY KEY (child_task_id, ordinal),
                UNIQUE (child_task_id, dependency_task_id)
            )
            """
        )
        connection.execute("INSERT INTO task_registry_meta VALUES (1, 2)")
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            (
                record["task_id"],
                record["project_id"],
                record["status"],
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
            ),
        )
        connection.execute("PRAGMA user_version=2")
        connection.commit()


def test_clean_t5_7_registry_migrates_to_v3(tmp_path: Path) -> None:
    from ai.tasks import initialize_task_registry

    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    _write_t5_7_v2_registry(
        registry,
        project_id=handle.project_id.value,
        status="CREATED",
        attempt=0,
    )

    initialize_task_registry(registry)

    with sqlite3.connect(registry) as connection:
        meta = connection.execute(
            "SELECT schema_version FROM task_registry_meta WHERE singleton=1"
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        )
    assert meta == (3,)
    assert user_version == 3
    assert {"execution_attempt", "started_at_epoch_s", "deadline_at_epoch_s"} <= set(columns)


def test_t5_7_running_registry_migration_fails_closed(tmp_path: Path) -> None:
    from ai.tasks import TaskStoreConflictError, initialize_task_registry

    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    _write_t5_7_v2_registry(
        registry,
        project_id=handle.project_id.value,
        status="RUNNING",
        attempt=1,
    )

    with pytest.raises(
        TaskStoreConflictError,
        match="cannot migrate RUNNING task",
    ):
        initialize_task_registry(registry)

    with sqlite3.connect(registry) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
