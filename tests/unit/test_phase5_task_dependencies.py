from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    MAX_TASK_DEPENDENCIES,
    MAX_TASK_DEPENDENCY_ISSUES,
    MAX_TASK_DIRECT_DEPENDENTS_RESULT,
    TASK_DEPENDENCY_CONTRACT_VERSION,
    TASK_DEPENDENCY_PERSISTENCE_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TASK_REGISTRY_SCHEMA_VERSION,
    MissingTaskDependency,
    TaskCreateInput,
    TaskDependencyConflictError,
    TaskDependencyStore,
    TaskStore,
)
from ai.contracts.task_states import TaskState


def make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    dependencies = TaskDependencyStore(projects)
    return projects, handle, tasks, dependencies


def task_input(*, title: str, dependencies=()) -> TaskCreateInput:
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


def test_contract_constants_are_explicit() -> None:
    assert TASK_DEPENDENCY_CONTRACT_VERSION == "1.0"
    assert TASK_DEPENDENCY_PERSISTENCE_CONTRACT_VERSION == "1.0"
    assert TASK_REGISTRY_SCHEMA_VERSION == 3
    assert MAX_TASK_DEPENDENCIES == 30
    assert MAX_TASK_DEPENDENCY_ISSUES == 4096
    assert MAX_TASK_DIRECT_DEPENDENTS_RESULT == 4096


def test_dependency_edges_are_persisted_in_declared_order(tmp_path: Path) -> None:
    projects, handle, tasks, dependencies = make_project(tmp_path)
    first = tasks.create_task(handle.project_id, task_input(title="First"))
    second = tasks.create_task(handle.project_id, task_input(title="Second"))
    child = tasks.create_task(
        handle.project_id,
        task_input(
            title="Child",
            dependencies=(second.task_id, first.task_id),
        ),
    )

    assert dependencies.dependencies(handle.project_id, child.task_id) == (
        second.task_id,
        first.task_id,
    )


def test_direct_dependents_are_queryable(tmp_path: Path) -> None:
    _projects, handle, tasks, dependencies = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input(title="Root"))
    a = tasks.create_task(
        handle.project_id,
        task_input(title="A", dependencies=(root.task_id,)),
    )
    b = tasks.create_task(
        handle.project_id,
        task_input(title="B", dependencies=(root.task_id,)),
    )

    assert dependencies.direct_dependents(handle.project_id, root.task_id) == (
        a.task_id,
        b.task_id,
    )


def test_verify_project_passes_when_every_dependency_exists(tmp_path: Path) -> None:
    _projects, handle, tasks, dependencies = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input(title="Root"))
    tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=(root.task_id,)),
    )
    dependencies.verify_project(handle.project_id)
    assert dependencies.missing_dependencies(handle.project_id) == ()


def test_missing_dependency_is_reported_but_task_remains_persisted(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, dependencies = make_project(tmp_path)
    child = tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=("TASK-999999",)),
    )

    assert tasks.open_task(handle.project_id, child.task_id) == child
    missing = dependencies.missing_dependencies(handle.project_id)
    assert missing == (
        MissingTaskDependency(
            child_task_id=child.task_id,
            dependency_task_id="TASK-999999",
        ),
    )
    with pytest.raises(
        TaskDependencyConflictError,
        match="does not exist",
    ):
        dependencies.verify_project(handle.project_id)


def test_cross_project_dependency_fails_same_project_existence_gate(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first = projects.create_project()
    second = projects.create_project()
    tasks = TaskStore(projects)
    dependency_store = TaskDependencyStore(projects)

    external = tasks.create_task(first.project_id, task_input(title="External"))
    tasks.create_task(
        second.project_id,
        task_input(title="Child", dependencies=(external.task_id,)),
    )

    with pytest.raises(TaskDependencyConflictError):
        dependency_store.verify_project(second.project_id)


def test_t5_3_deliberately_does_not_detect_self_cycle(tmp_path: Path) -> None:
    _projects, handle, tasks, dependencies = make_project(tmp_path)
    # First allocated ID is predictably TASK-000001. The dependency exists once
    # the same record is committed. T5.3 checks existence only; T5.4 owns DAG
    # cycle detection and must reject this graph before execution.
    task = tasks.create_task(
        handle.project_id,
        task_input(title="Self", dependencies=("TASK-000001",)),
    )
    assert task.task_id.value == "TASK-000001"
    dependencies.verify_project(handle.project_id)
    assert dependencies.dependencies(handle.project_id, task.task_id) == (
        task.task_id,
    )


def test_dependency_resolution_does_not_change_task_state(tmp_path: Path) -> None:
    _projects, handle, tasks, dependencies = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input(title="Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=(root.task_id,)),
    )

    dependencies.verify_project(handle.project_id)
    assert tasks.open_task(handle.project_id, root.task_id).status is TaskState.CREATED
    assert tasks.open_task(handle.project_id, child.task_id).status is TaskState.CREATED


def test_restart_preserves_dependency_edges(tmp_path: Path) -> None:
    projects, handle, tasks, _dependencies = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input(title="Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=(root.task_id,)),
    )

    restarted_projects = ProjectStore(projects.projects_root)
    restarted = TaskDependencyStore(restarted_projects)
    restarted.verify_project(handle.project_id)
    assert restarted.dependencies(handle.project_id, child.task_id) == (
        root.task_id,
    )


def test_edge_tampering_fails_closed(tmp_path: Path) -> None:
    _projects, handle, tasks, dependencies = make_project(tmp_path)
    root = tasks.create_task(handle.project_id, task_input(title="Root"))
    child = tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=(root.task_id,)),
    )
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME

    with sqlite3.connect(registry) as connection:
        connection.execute(
            "UPDATE task_dependency_edges SET dependency_task_id='TASK-999999' "
            "WHERE child_task_id=?",
            (child.task_id.value,),
        )
        connection.commit()

    with pytest.raises(Exception, match="do not match immutable task record"):
        dependencies.dependencies(handle.project_id, child.task_id)


def test_clean_t5_2_registry_migrates_to_v2_and_backfills_edges(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME

    root_record = {
        "task_id": "TASK-000001",
        "project_id": handle.project_id.value,
        "title": "Root",
        "role": "uav_systems_engineer",
        "priority": 100,
        "dependencies": [],
        "input_artifacts": [],
        "output_schema": "engineering_task.v1",
        "validators": ["schema"],
        "review_required": False,
        "attempt": 0,
        "max_attempts": 3,
        "status": "CREATED",
    }
    child_record = dict(root_record)
    child_record.update({
        "task_id": "TASK-000002",
        "title": "Child",
        "dependencies": ["TASK-000001"],
    })

    registry.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(registry) as connection:
        connection.execute(
            """
            CREATE TABLE task_registry_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1)
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
        connection.execute("INSERT INTO task_registry_meta VALUES (1, 1)")
        for record in (root_record, child_record):
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?)",
                (
                    record["task_id"],
                    record["project_id"],
                    record["status"],
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
                ),
            )
        connection.execute("PRAGMA user_version=1")
        connection.commit()

    from ai.tasks import initialize_task_registry
    initialize_task_registry(registry)

    with sqlite3.connect(registry) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        meta = connection.execute(
            "SELECT schema_version FROM task_registry_meta WHERE singleton=1"
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        edges = connection.execute(
            "SELECT child_task_id, ordinal, dependency_task_id "
            "FROM task_dependency_edges ORDER BY child_task_id, ordinal"
        ).fetchall()

    assert tables == (
        "task_dependency_edges",
        "task_registry_meta",
        "tasks",
    )
    assert meta == (3,)
    assert user_version == 3
    assert edges == [("TASK-000002", 0, "TASK-000001")]


def test_registry_and_phase3_schema_sizes_are_stable(tmp_path: Path) -> None:
    projects, handle, tasks, _dependencies = make_project(tmp_path)
    tasks.create_task(handle.project_id, task_input(title="Root"))
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME

    with sqlite3.connect(registry) as connection:
        tables = tuple(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )

    assert tables == ("task_dependency_edges", "task_registry_meta", "tasks")
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_t5_3_dependency_api_exposes_no_cycle_ready_or_scheduler_behavior(
    tmp_path: Path,
) -> None:
    projects, _handle, _tasks, dependencies = make_project(tmp_path)
    for name in (
        "validate_dag",
        "detect_cycle",
        "resolve_ready",
        "mark_ready",
        "enqueue",
        "next_ready",
        "increment_attempt",
        "timeout",
        "recover_interrupted",
        "cancel_task",
        "run",
    ):
        assert not hasattr(dependencies, name)
