from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import ArtifactInputVersion
from ai.contracts.ids import IdKind
from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    MAX_TASK_ATTEMPTS,
    MAX_TASK_DEPENDENCIES,
    MAX_TASK_INPUT_ARTIFACTS,
    MAX_TASK_PRIORITY,
    MAX_TASK_TITLE_BYTES,
    TASK_RECORD_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TASK_REGISTRY_SCHEMA_VERSION,
    TASK_STORE_CONTRACT_VERSION,
    TaskCreateInput,
    TaskNotFoundError,
    TaskRecord,
    TaskRecordError,
    TaskStore,
    TaskStoreConflictError,
)


def make_store(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    return TaskStore(projects), projects, handle


def task_input(**overrides) -> TaskCreateInput:
    values = {
        "title": "Build mass budget",
        "role": "uav_systems_engineer",
        "priority": 100,
        "dependencies": (),
        "input_artifacts": (),
        "output_schema": "mass_budget.v1",
        "validators": ("schema", "finite_values"),
        "review_required": True,
        "max_attempts": 3,
    }
    values.update(overrides)
    return TaskCreateInput(**values)


def test_contract_constants_are_explicit() -> None:
    assert TASK_RECORD_CONTRACT_VERSION == "1.0"
    assert TASK_STORE_CONTRACT_VERSION == "1.2"
    assert TASK_REGISTRY_SCHEMA_VERSION == 3
    assert MAX_TASK_ATTEMPTS == 3
    assert MAX_TASK_DEPENDENCIES == 30
    assert MAX_TASK_INPUT_ARTIFACTS == 64
    assert MAX_TASK_PRIORITY == 1_000_000
    assert MAX_TASK_TITLE_BYTES == 512


def test_create_allocates_task_id_and_forces_created_attempt_zero(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(handle.project_id, task_input())
    assert record.task_id.value == "TASK-000001"
    assert record.project_id == handle.project_id
    assert record.status is TaskState.CREATED
    assert record.attempt == 0
    assert record.max_attempts == 3


def test_task_ids_are_global_across_projects(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first = projects.create_project()
    second = projects.create_project()
    store = TaskStore(projects)
    a = store.create_task(first.project_id, task_input())
    b = store.create_task(second.project_id, task_input(title="Second task"))
    assert a.task_id.value == "TASK-000001"
    assert b.task_id.value == "TASK-000002"


def test_task_sequence_uses_existing_phase3_allocator(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    store.create_task(handle.project_id, task_input())
    with sqlite3.connect(projects.database_path) as connection:
        row = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
            (IdKind.TASK.value,),
        ).fetchone()
    assert row == (1,)


def test_registry_lives_inside_reserved_tasks_directory(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    store.create_task(handle.project_id, task_input())
    path = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    assert path.is_file()


def test_registry_has_three_tables_and_phase3_schema_is_unchanged(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    store.create_task(handle.project_id, task_input())
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert tables == ("task_dependency_edges", "task_registry_meta", "tasks")
    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_exact_minimum_task_contract_round_trips(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(
        handle.project_id,
        task_input(
            dependencies=("TASK-000099",),
            input_artifacts=(
                ArtifactInputVersion(entity_id="ART-000001", version=2),
            ),
        ),
    )
    assert set(record.to_dict()) == {
        "task_id",
        "project_id",
        "title",
        "role",
        "priority",
        "dependencies",
        "input_artifacts",
        "output_schema",
        "validators",
        "review_required",
        "attempt",
        "max_attempts",
        "status",
    }
    assert TaskRecord.from_dict(record.to_dict()) == record


def test_restart_reopens_same_task(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    record = store.create_task(handle.project_id, task_input())
    restarted = TaskStore(ProjectStore(projects.projects_root))
    assert restarted.open_task(handle.project_id, record.task_id) == record


def test_list_tasks_is_deterministic_by_id(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_task(handle.project_id, task_input(title="A"))
    second = store.create_task(handle.project_id, task_input(title="B"))
    assert store.list_tasks(handle.project_id) == (first, second)


def test_list_empty_project_is_empty(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    assert store.list_tasks(handle.project_id) == ()


def test_missing_task_is_not_found(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    with pytest.raises(TaskNotFoundError):
        store.open_task(handle.project_id, "TASK-000001")


def test_role_is_syntax_checked_but_not_known_role_validated(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(
        handle.project_id,
        task_input(role="future_specialist_role"),
    )
    assert record.role == "future_specialist_role"


@pytest.mark.parametrize("role", ["", "Reviewer", "bad role", "_reviewer"])
def test_invalid_role_syntax_is_rejected(tmp_path: Path, role: str) -> None:
    _store, _projects, _handle = make_store(tmp_path)
    with pytest.raises(TaskStoreConflictError):
        task_input(role=role)


def test_dependency_declarations_are_persisted_before_t5_3_existence_gate(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(
        handle.project_id,
        task_input(dependencies=("TASK-999999",)),
    )
    assert record.dependencies[0].value == "TASK-999999"


def test_input_artifact_declarations_do_not_require_existence_in_t5_1(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(
        handle.project_id,
        task_input(
            input_artifacts=(
                ArtifactInputVersion(entity_id="ART-999999", version=1),
            )
        ),
    )
    assert record.input_artifacts[0].entity_id.value == "ART-999999"


def test_dependencies_are_bounded() -> None:
    deps = tuple(f"TASK-{i:06d}" for i in range(1, MAX_TASK_DEPENDENCIES + 1))
    assert len(task_input(dependencies=deps).dependencies) == MAX_TASK_DEPENDENCIES
    with pytest.raises(TaskStoreConflictError):
        task_input(dependencies=deps + ("TASK-000031",))


def test_duplicate_dependency_is_rejected() -> None:
    with pytest.raises(TaskStoreConflictError):
        task_input(dependencies=("TASK-000001", "TASK-000001"))


def test_input_artifacts_are_bounded() -> None:
    artifacts = tuple(
        ArtifactInputVersion(entity_id=f"ART-{i:06d}", version=1)
        for i in range(1, MAX_TASK_INPUT_ARTIFACTS + 1)
    )
    assert len(task_input(input_artifacts=artifacts).input_artifacts) == MAX_TASK_INPUT_ARTIFACTS
    with pytest.raises(TaskStoreConflictError):
        task_input(
            input_artifacts=artifacts
            + (ArtifactInputVersion(entity_id="ART-000065", version=1),)
        )


def test_max_attempts_is_bounded_to_frozen_default_limit() -> None:
    assert task_input(max_attempts=1).max_attempts == 1
    assert task_input(max_attempts=3).max_attempts == 3
    for value in (0, 4, -1, True):
        with pytest.raises(TaskStoreConflictError):
            task_input(max_attempts=value)


def test_priority_is_bounded() -> None:
    assert task_input(priority=0).priority == 0
    assert task_input(priority=MAX_TASK_PRIORITY).priority == MAX_TASK_PRIORITY
    for value in (-1, MAX_TASK_PRIORITY + 1, True, 1.5):
        with pytest.raises(TaskStoreConflictError):
            task_input(priority=value)


def test_title_is_bounded_in_utf8_bytes() -> None:
    assert len(task_input(title="a" * 512).title.encode("utf-8")) == 512
    with pytest.raises(TaskStoreConflictError):
        task_input(title="a" * 513)


def test_create_does_not_accept_caller_status_or_attempt() -> None:
    assert "status" not in TaskCreateInput.__dataclass_fields__
    assert "attempt" not in TaskCreateInput.__dataclass_fields__


def test_record_parser_accepts_all_canonical_task_states_for_future_persistence() -> None:
    base = TaskRecord(
        task_id="TASK-000001",
        project_id="UAV-PROJ-000001",
        title="Task",
        role="reviewer",
        priority=1,
        dependencies=(),
        input_artifacts=(),
        output_schema="review.v1",
        validators=(),
        review_required=False,
        attempt=0,
        max_attempts=3,
        status=TaskState.CREATED,
    ).to_dict()
    for state in TaskState:
        payload = dict(base)
        payload["status"] = state.value
        assert TaskRecord.from_dict(payload).status is state


def test_task_registry_status_tampering_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(handle.project_id, task_input())
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        connection.execute(
            "UPDATE tasks SET status='READY' WHERE task_id=?",
            (record.task_id.value,),
        )
        connection.commit()
    with pytest.raises(TaskStoreConflictError, match="status index mismatch"):
        store.open_task(handle.project_id, record.task_id)


def test_task_record_json_tampering_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    record = store.create_task(handle.project_id, task_input())
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        connection.execute(
            "UPDATE tasks SET record_json='{" + '"bad":true' + "}' WHERE task_id=?",
            (record.task_id.value,),
        )
        connection.commit()
    with pytest.raises(TaskStoreConflictError):
        store.open_task(handle.project_id, record.task_id)


def test_registry_symlink_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"")
    registry.symlink_to(outside)
    with pytest.raises(TaskStoreConflictError, match="symbolic link"):
        store.create_task(handle.project_id, task_input())


def test_task_store_still_exposes_no_scheduler_retry_or_recovery_api(tmp_path: Path) -> None:
    store, _projects, _handle = make_store(tmp_path)
    for name in (
        "transition",
        "resolve_ready",
        "add_dependency",
        "validate_dag",
        "enqueue",
        "next_ready",
        "increment_attempt",
        "timeout",
        "recover_interrupted",
        "cancel",
        "run",
    ):
        assert not hasattr(store, name)
