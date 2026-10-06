from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai.agents import (
    DEFAULT_ROLE_REGISTRY,
    TASK_AGENT_MAPPING_CONTRACT_VERSION,
    AgentInstance,
    RoleDefinition,
    RoleRegistry,
    TaskAgentBinding,
    TaskAgentMapper,
    TaskAgentMappingConflictError,
    TaskAgentUnknownRoleError,
)
from ai.project.store import ProjectStore
from ai.tasks import (
    TaskCreateInput,
    TaskLifecycleStore,
    TaskStore,
)
from ai.contracts.task_states import TaskState


def task_input(
    *,
    title: str,
    role: str,
) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role=role,
        priority=100,
        dependencies=(),
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
    mapper = TaskAgentMapper(projects)
    return projects, handle, tasks, mapper


def test_contract_version_is_explicit() -> None:
    assert TASK_AGENT_MAPPING_CONTRACT_VERSION == "1.0"


@pytest.mark.parametrize(
    "role_id",
    (
        "coordinator",
        "requirements_engineer",
        "uav_systems_engineer",
        "reviewer",
    ),
)
def test_persisted_mvp_task_maps_to_same_role_agent(
    tmp_path: Path,
    role_id: str,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title=f"Task for {role_id}",
            role=role_id,
        ),
    )

    binding = mapper.map_task(
        handle.project_id,
        task.task_id,
    )

    assert binding.project_id == handle.project_id
    assert binding.task_id == task.task_id
    assert binding.role_id == role_id
    assert binding.agent.role_id == role_id
    assert binding.agent.instance_key == task.task_id.value
    assert binding.agent.role is DEFAULT_ROLE_REGISTRY.get(role_id)


def test_same_role_tasks_map_to_distinct_agent_instances(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)

    first = tasks.create_task(
        handle.project_id,
        task_input(
            title="First UAV systems task",
            role="uav_systems_engineer",
        ),
    )
    second = tasks.create_task(
        handle.project_id,
        task_input(
            title="Second UAV systems task",
            role="uav_systems_engineer",
        ),
    )

    first_binding = mapper.map_task(
        handle.project_id,
        first.task_id,
    )
    second_binding = mapper.map_task(
        handle.project_id,
        second.task_id,
    )

    assert first_binding.agent != second_binding.agent
    assert first_binding.agent.role is second_binding.agent.role
    assert first_binding.agent.instance_key == first.task_id.value
    assert second_binding.agent.instance_key == second.task_id.value


def test_mapping_is_deterministic_and_repeatable(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Requirements task",
            role="requirements_engineer",
        ),
    )

    first = mapper.map_task(handle.project_id, task.task_id)
    second = mapper.map_task(handle.project_id, task.task_id)

    assert first == second


def test_mapping_does_not_depend_on_task_title(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Reviewer-like words in title but stored role wins",
            role="coordinator",
        ),
    )

    binding = mapper.map_task(handle.project_id, task.task_id)

    assert binding.role_id == "coordinator"


def test_unknown_task_role_fails_closed(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Unknown role task",
            role="invented_engineer",
        ),
    )

    with pytest.raises(
        TaskAgentUnknownRoleError,
        match="not enabled",
    ):
        mapper.map_task(
            handle.project_id,
            task.task_id,
        )


@pytest.mark.parametrize(
    "reserved_role",
    (
        "propulsion_power_engineer",
        "airframe_engineer",
        "px4_flight_engineer",
        "software_engineer",
        "simulation_engineer",
        "verification_engineer",
    ),
)
def test_reserved_specialized_task_role_fails_closed(
    tmp_path: Path,
    reserved_role: str,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title=f"Reserved {reserved_role}",
            role=reserved_role,
        ),
    )

    with pytest.raises(TaskAgentUnknownRoleError):
        mapper.map_task(
            handle.project_id,
            task.task_id,
        )


def test_missing_task_fails_closed(
    tmp_path: Path,
) -> None:
    _projects, handle, _tasks, mapper = make_project(tmp_path)

    with pytest.raises(
        TaskAgentMappingConflictError,
        match="cannot be opened safely",
    ):
        mapper.map_task(
            handle.project_id,
            "TASK-999999",
        )


def test_wrong_project_fails_closed(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    first_project = projects.create_project()
    second_project = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(
        first_project.project_id,
        task_input(
            title="First project task",
            role="reviewer",
        ),
    )

    with pytest.raises(TaskAgentMappingConflictError):
        TaskAgentMapper(projects).map_task(
            second_project.project_id,
            task.task_id,
        )


def test_map_record_requires_task_record(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    mapper = TaskAgentMapper(projects)

    with pytest.raises(TaskAgentMappingConflictError):
        mapper.map_record(object())  # type: ignore[arg-type]


def test_binding_rejects_agent_key_that_differs_from_task_id() -> None:
    role = DEFAULT_ROLE_REGISTRY.get("reviewer")
    agent = AgentInstance(
        instance_key="TASK-000002",
        role=role,
    )

    with pytest.raises(
        TaskAgentMappingConflictError,
        match="instance_key",
    ):
        TaskAgentBinding(
            project_id="UAV-PROJ-000001",
            task_id="TASK-000001",
            role_id="reviewer",
            agent=agent,
        )


def test_binding_rejects_role_mismatch() -> None:
    agent = AgentInstance(
        instance_key="TASK-000001",
        role=DEFAULT_ROLE_REGISTRY.get("reviewer"),
    )

    with pytest.raises(
        TaskAgentMappingConflictError,
        match="role_id",
    ):
        TaskAgentBinding(
            project_id="UAV-PROJ-000001",
            task_id="TASK-000001",
            role_id="coordinator",
            agent=agent,
        )


def test_custom_registry_can_enable_one_custom_role_without_global_mutation(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    tasks = TaskStore(projects)

    custom = RoleDefinition(
        role_id="custom_mapper_role",
        title="Custom Mapper Role",
        purpose="Used only to verify injected registry mapping.",
    )
    mapper = TaskAgentMapper(
        projects,
        RoleRegistry((custom,)),
    )
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Custom role task",
            role="custom_mapper_role",
        ),
    )

    binding = mapper.map_task(
        handle.project_id,
        task.task_id,
    )

    assert binding.role_id == "custom_mapper_role"
    assert not DEFAULT_ROLE_REGISTRY.contains("custom_mapper_role")


def test_mapping_does_not_mutate_task_status_or_attempt(
    tmp_path: Path,
) -> None:
    _projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Stable task",
            role="coordinator",
        ),
    )

    before = tasks.open_task(handle.project_id, task.task_id)
    mapper.map_task(handle.project_id, task.task_id)
    after = tasks.open_task(handle.project_id, task.task_id)

    assert before == after
    assert after.status is TaskState.CREATED
    assert after.attempt == 0


def test_mapping_remains_same_after_task_status_progression(
    tmp_path: Path,
) -> None:
    projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Lifecycle-independent identity",
            role="reviewer",
        ),
    )
    initial = mapper.map_task(handle.project_id, task.task_id)

    TaskLifecycleStore(projects).transition_task(
        handle.project_id,
        task.task_id,
        TaskState.READY,
    )
    later = mapper.map_task(handle.project_id, task.task_id)

    assert initial == later


def test_mapping_survives_restart(
    tmp_path: Path,
) -> None:
    projects, handle, tasks, mapper = make_project(tmp_path)
    task = tasks.create_task(
        handle.project_id,
        task_input(
            title="Restarted mapping",
            role="uav_systems_engineer",
        ),
    )

    first = mapper.map_task(handle.project_id, task.task_id)

    restarted_projects = ProjectStore(projects.projects_root)
    second = TaskAgentMapper(restarted_projects).map_task(
        handle.project_id,
        task.task_id,
    )

    assert second == first


def test_mapper_exposes_no_execution_context_provider_or_persistence_api(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    mapper = TaskAgentMapper(projects)

    for name in (
        "run",
        "execute",
        "generate",
        "assign",
        "enqueue",
        "build_context",
        "invoke_model",
        "persist_run",
        "create_artifact",
        "approve",
        "verify_uav",
    ):
        assert not hasattr(mapper, name)
