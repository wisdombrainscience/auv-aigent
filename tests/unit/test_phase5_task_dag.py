from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

import ai.tasks.dag as dag_module
from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    MAX_TASK_DAG_EDGES,
    MAX_TASK_DAG_NODES,
    TASK_DAG_CONTRACT_VERSION,
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskDAGConflictError,
    TaskDAGCycleError,
    TaskDAGValidator,
    TaskStore,
)


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    tasks = TaskStore(projects)
    dag = TaskDAGValidator(projects)
    return projects, handle, tasks, dag


def task_input(
    *,
    title: str,
    dependencies=(),
    priority: int = 100,
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


def values(items):
    return tuple(item.value for item in items)


def test_contract_constants_are_explicit() -> None:
    assert TASK_DAG_CONTRACT_VERSION == "1.0"
    assert MAX_TASK_DAG_NODES == 4096
    assert MAX_TASK_DAG_EDGES == 4096 * 30


def test_empty_project_is_valid_empty_dag(tmp_path: Path) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    report = TaskDAGValidator(projects).validate_project(
        handle.project_id
    )

    assert report.node_count == 0
    assert report.edge_count == 0
    assert report.topological_order == ()


def test_chain_has_deterministic_topological_order(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    root = tasks.create_task(
        handle.project_id,
        task_input(title="Root"),
    )
    middle = tasks.create_task(
        handle.project_id,
        task_input(
            title="Middle",
            dependencies=(root.task_id,),
        ),
    )
    leaf = tasks.create_task(
        handle.project_id,
        task_input(
            title="Leaf",
            dependencies=(middle.task_id,),
        ),
    )

    report = dag.validate_project(handle.project_id)

    assert report.node_count == 3
    assert report.edge_count == 2
    assert values(report.topological_order) == (
        root.task_id.value,
        middle.task_id.value,
        leaf.task_id.value,
    )


def test_diamond_is_valid_and_stably_ordered(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    root = tasks.create_task(
        handle.project_id,
        task_input(title="Root"),
    )
    left = tasks.create_task(
        handle.project_id,
        task_input(title="Left", dependencies=(root.task_id,)),
    )
    right = tasks.create_task(
        handle.project_id,
        task_input(title="Right", dependencies=(root.task_id,)),
    )
    join = tasks.create_task(
        handle.project_id,
        task_input(
            title="Join",
            dependencies=(left.task_id, right.task_id),
        ),
    )

    report = dag.validate_project(handle.project_id)

    assert report.edge_count == 4
    assert values(report.topological_order) == (
        root.task_id.value,
        left.task_id.value,
        right.task_id.value,
        join.task_id.value,
    )


def test_disconnected_roots_use_canonical_task_order(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    first = tasks.create_task(
        handle.project_id,
        task_input(title="First"),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input(title="Second"),
    )

    report = dag.validate_project(handle.project_id)

    assert values(report.topological_order) == (
        first.task_id.value,
        second.task_id.value,
    )


def test_self_cycle_is_detected_with_stable_witness(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Self",
            dependencies=("TASK-000001",),
        ),
    )

    cycle = dag.detect_cycle(handle.project_id)
    assert cycle is not None
    assert values(cycle.task_ids) == (
        task.task_id.value,
        task.task_id.value,
    )

    with pytest.raises(TaskDAGCycleError) as info:
        dag.validate_project(handle.project_id)
    assert info.value.cycle == cycle


def test_two_node_cycle_is_detected(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)

    first = tasks.create_task(
        handle.project_id,
        task_input(
            title="A",
            dependencies=("TASK-000002",),
        ),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input(
            title="B",
            dependencies=(first.task_id,),
        ),
    )

    with pytest.raises(TaskDAGCycleError) as info:
        dag.validate_project(handle.project_id)

    assert values(info.value.cycle.task_ids) == (
        first.task_id.value,
        second.task_id.value,
        first.task_id.value,
    )


def test_three_node_cycle_is_detected(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)

    a = tasks.create_task(
        handle.project_id,
        task_input(title="A", dependencies=("TASK-000002",)),
    )
    b = tasks.create_task(
        handle.project_id,
        task_input(title="B", dependencies=("TASK-000003",)),
    )
    c = tasks.create_task(
        handle.project_id,
        task_input(title="C", dependencies=(a.task_id,)),
    )

    with pytest.raises(TaskDAGCycleError) as info:
        dag.validate_project(handle.project_id)

    assert values(info.value.cycle.task_ids) == (
        a.task_id.value,
        b.task_id.value,
        c.task_id.value,
        a.task_id.value,
    )


def test_cycle_witness_is_deterministic_when_multiple_cycles_exist(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)

    a = tasks.create_task(
        handle.project_id,
        task_input(title="A", dependencies=("TASK-000002",)),
    )
    b = tasks.create_task(
        handle.project_id,
        task_input(title="B", dependencies=(a.task_id,)),
    )
    c = tasks.create_task(
        handle.project_id,
        task_input(title="C", dependencies=("TASK-000004",)),
    )
    tasks.create_task(
        handle.project_id,
        task_input(title="D", dependencies=(c.task_id,)),
    )

    cycle = dag.detect_cycle(handle.project_id)
    assert cycle is not None
    assert values(cycle.task_ids) == (
        a.task_id.value,
        b.task_id.value,
        a.task_id.value,
    )


def test_missing_dependency_fails_before_dag_classification(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    tasks.create_task(
        handle.project_id,
        task_input(
            title="Missing",
            dependencies=("TASK-999999",),
        ),
    )

    with pytest.raises(
        TaskDAGConflictError,
        match="does not exist",
    ):
        dag.validate_project(handle.project_id)


def test_cross_project_dependency_fails_closed(tmp_path: Path) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    first_project = projects.create_project()
    second_project = projects.create_project()
    tasks = TaskStore(projects)

    external = tasks.create_task(
        first_project.project_id,
        task_input(title="External"),
    )
    tasks.create_task(
        second_project.project_id,
        task_input(
            title="Child",
            dependencies=(external.task_id,),
        ),
    )

    with pytest.raises(TaskDAGConflictError):
        TaskDAGValidator(projects).validate_project(
            second_project.project_id
        )


def test_validation_does_not_change_status_or_attempt(tmp_path: Path) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    root = tasks.create_task(
        handle.project_id,
        task_input(title="Root"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=(root.task_id,)),
    )

    before = tasks.list_tasks(handle.project_id)
    dag.validate_project(handle.project_id)
    after = tasks.list_tasks(handle.project_id)

    assert after == before
    assert all(item.status is TaskState.CREATED for item in after)
    assert all(item.attempt == 0 for item in after)
    assert child.status is TaskState.CREATED


def test_restart_preserves_dag_result(tmp_path: Path) -> None:
    projects, handle, tasks, dag = make_project(tmp_path)
    root = tasks.create_task(
        handle.project_id,
        task_input(title="Root"),
    )
    child = tasks.create_task(
        handle.project_id,
        task_input(title="Child", dependencies=(root.task_id,)),
    )

    first = dag.validate_project(handle.project_id)

    restarted_projects = ProjectStore(projects.projects_root)
    restarted = TaskDAGValidator(restarted_projects)
    second = restarted.validate_project(handle.project_id)

    assert second == first
    assert values(second.topological_order) == (
        root.task_id.value,
        child.task_id.value,
    )


def test_node_scan_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    for title in ("A", "B", "C"):
        tasks.create_task(
            handle.project_id,
            task_input(title=title),
        )

    monkeypatch.setattr(dag_module, "MAX_TASK_DAG_NODES", 2)

    with pytest.raises(
        TaskDAGConflictError,
        match="MAX_TASK_DAG_NODES",
    ):
        dag.validate_project(handle.project_id)


def test_edge_scan_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _projects, handle, tasks, dag = make_project(tmp_path)
    root = tasks.create_task(
        handle.project_id,
        task_input(title="Root"),
    )
    tasks.create_task(
        handle.project_id,
        task_input(title="A", dependencies=(root.task_id,)),
    )
    tasks.create_task(
        handle.project_id,
        task_input(title="B", dependencies=(root.task_id,)),
    )

    monkeypatch.setattr(dag_module, "MAX_TASK_DAG_EDGES", 1)

    with pytest.raises(
        TaskDAGConflictError,
        match="MAX_TASK_DAG_EDGES",
    ):
        dag.validate_project(handle.project_id)


def test_registry_schema_is_unchanged_and_phase3_schema_remains_three_tables(
    tmp_path: Path,
) -> None:
    projects, handle, tasks, dag = make_project(tmp_path)
    tasks.create_task(
        handle.project_id,
        task_input(title="Root"),
    )
    dag.validate_project(handle.project_id)

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


def test_t5_4_api_exposes_no_ready_scheduler_or_execution_behavior(
    tmp_path: Path,
) -> None:
    projects, _handle, _tasks, dag = make_project(tmp_path)

    for name in (
        "resolve_ready",
        "mark_ready",
        "mark_blocked",
        "enqueue",
        "next_ready",
        "increment_attempt",
        "timeout",
        "recover_interrupted",
        "cancel_task",
        "run",
    ):
        assert not hasattr(dag, name)
