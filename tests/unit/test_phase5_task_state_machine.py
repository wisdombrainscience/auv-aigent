from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.contracts.task_states import (
    HUMAN_GATE_TASK_STATES,
    TERMINAL_TASK_STATES,
    TaskState,
    allowed_task_transitions,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    TASK_LIFECYCLE_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskStore,
    TaskTransitionConflictError,
)


def task_input(*, review_required: bool = False) -> TaskCreateInput:
    return TaskCreateInput(
        title="Analyze propulsion requirement",
        role="uav_systems_engineer",
        priority=10,
        dependencies=(),
        input_artifacts=(),
        output_schema="engineering.analysis.v1",
        validators=("finite_values",),
        review_required=review_required,
        max_attempts=3,
    )


def make_store(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    record = tasks.create_task(handle.project_id, task_input())
    lifecycle = TaskLifecycleStore(projects)
    return projects, handle, tasks, lifecycle, record


def advance(lifecycle, project_id, task_id, *states):
    current = None
    for state in states:
        current = lifecycle.transition_task(project_id, task_id, state)
    return current


def test_contract_version_is_explicit() -> None:
    assert TASK_LIFECYCLE_CONTRACT_VERSION == "1.2"


def test_created_to_done_direct_non_review_path(tmp_path: Path) -> None:
    _projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    final = advance(
        lifecycle,
        handle.project_id,
        record.task_id,
        TaskState.READY,
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.VALIDATING,
        TaskState.DONE,
    )
    assert final.status is TaskState.DONE
    assert final.attempt == 1


def test_review_required_path_can_use_reviewing(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    record = tasks.create_task(handle.project_id, task_input(review_required=True))
    lifecycle = TaskLifecycleStore(projects)

    final = advance(
        lifecycle,
        handle.project_id,
        record.task_id,
        TaskState.READY,
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.REVIEWING,
        TaskState.VALIDATING,
        TaskState.DONE,
    )
    assert final.status is TaskState.DONE


def test_every_frozen_direct_transition_is_executable(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    lifecycle = TaskLifecycleStore(projects)

    for source in TaskState:
        for target in allowed_task_transitions(source):
            record = tasks.create_task(
                handle.project_id,
                TaskCreateInput(
                    title=f"{source.value} to {target.value}",
                    role="reviewer",
                    priority=1,
                    dependencies=(),
                    input_artifacts=(),
                    output_schema="review.v1",
                    validators=(),
                    review_required=False,
                    max_attempts=3,
                ),
            )
            # Persist source directly for transition-contract isolation. The
            # record parser already accepts every canonical state; this setup
            # avoids requiring a path from CREATED to every source.
            registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
            source_record = record.__class__(**{
                **record.__dict__,
                "status": source,
                "attempt": 1 if source is TaskState.RUNNING else record.attempt,
            })
            import json
            raw = json.dumps(
                source_record.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
            with sqlite3.connect(registry) as connection:
                if source is TaskState.RUNNING:
                    connection.execute(
                        """
                        UPDATE tasks
                        SET status=?, record_json=?,
                            execution_attempt=1,
                            started_at_epoch_s=1000.0,
                            deadline_at_epoch_s=1300.0
                        WHERE task_id=?
                        """,
                        (source.value, raw, record.task_id.value),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE tasks
                        SET status=?, record_json=?,
                            execution_attempt=NULL,
                            started_at_epoch_s=NULL,
                            deadline_at_epoch_s=NULL
                        WHERE task_id=?
                        """,
                        (source.value, raw, record.task_id.value),
                    )
                connection.commit()

            updated = lifecycle.transition_task(
                handle.project_id,
                record.task_id,
                target,
            )
            assert updated.status is target


def test_invalid_transition_fails_without_mutation(tmp_path: Path) -> None:
    _projects, handle, tasks, lifecycle, record = make_store(tmp_path)
    with pytest.raises(TaskTransitionConflictError, match="invalid task transition"):
        lifecycle.transition_task(handle.project_id, record.task_id, TaskState.RUNNING)
    assert tasks.open_task(handle.project_id, record.task_id).status is TaskState.CREATED


def test_self_transition_is_invalid(tmp_path: Path) -> None:
    _projects, handle, tasks, lifecycle, record = make_store(tmp_path)
    with pytest.raises(TaskTransitionConflictError):
        lifecycle.transition_task(handle.project_id, record.task_id, TaskState.CREATED)
    assert tasks.open_task(handle.project_id, record.task_id).status is TaskState.CREATED


def test_terminal_states_have_no_successors(tmp_path: Path) -> None:
    assert TERMINAL_TASK_STATES == {
        TaskState.DONE,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
    for state in TERMINAL_TASK_STATES:
        assert allowed_task_transitions(state) == frozenset()


def test_needs_human_is_not_terminal(tmp_path: Path) -> None:
    assert HUMAN_GATE_TASK_STATES == {TaskState.NEEDS_HUMAN}
    assert TaskState.READY in allowed_task_transitions(TaskState.NEEDS_HUMAN)
    assert TaskState.BLOCKED in allowed_task_transitions(TaskState.NEEDS_HUMAN)


def test_rework_path_is_persisted(tmp_path: Path) -> None:
    _projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    updated = advance(
        lifecycle,
        handle.project_id,
        record.task_id,
        TaskState.READY,
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.REWORK,
        TaskState.READY,
    )
    assert updated.status is TaskState.READY


def test_interrupted_state_can_be_manually_transitioned_but_attempt_unchanged(tmp_path: Path) -> None:
    _projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    updated = advance(
        lifecycle,
        handle.project_id,
        record.task_id,
        TaskState.READY,
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.INTERRUPTED,
    )
    assert updated.status is TaskState.INTERRUPTED
    assert updated.attempt == 1


def test_generic_cancelled_transition_uses_frozen_edge_only(tmp_path: Path) -> None:
    _projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    updated = lifecycle.transition_task(
        handle.project_id,
        record.task_id,
        TaskState.CANCELLED,
    )
    assert updated.status is TaskState.CANCELLED
    assert lifecycle.allowed_transitions(handle.project_id, record.task_id) == frozenset()


def test_transition_changes_only_status(tmp_path: Path) -> None:
    _projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    updated = lifecycle.transition_task(handle.project_id, record.task_id, TaskState.READY)
    before = record.to_dict()
    after = updated.to_dict()
    assert after.pop("status") == "READY"
    assert before.pop("status") == "CREATED"
    assert after == before


def test_restart_preserves_transitioned_status(tmp_path: Path) -> None:
    projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    lifecycle.transition_task(handle.project_id, record.task_id, TaskState.READY)
    restarted_projects = ProjectStore(projects.projects_root)
    reopened = TaskStore(restarted_projects).open_task(handle.project_id, record.task_id)
    assert reopened.status is TaskState.READY


def test_registry_and_phase3_schema_are_unchanged(tmp_path: Path) -> None:
    projects, handle, _tasks, lifecycle, record = make_store(tmp_path)
    lifecycle.transition_task(handle.project_id, record.task_id, TaskState.READY)
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        tables = tuple(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert tables == ("task_dependency_edges", "task_registry_meta", "tasks")
    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_t5_2_exposes_no_dependency_scheduler_attempt_timeout_or_recovery_api(tmp_path: Path) -> None:
    projects, _handle, _tasks, lifecycle, _record = make_store(tmp_path)
    for name in (
        "resolve_dependencies",
        "validate_dag",
        "resolve_ready",
        "enqueue",
        "next_ready",
        "increment_attempt",
        "timeout",
        "recover_interrupted",
        "cancel_task",
        "run",
    ):
        assert not hasattr(lifecycle, name)
