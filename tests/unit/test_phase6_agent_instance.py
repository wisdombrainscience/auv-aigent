from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ai.agents import (
    AGENT_INSTANCE_CONTRACT_VERSION,
    DEFAULT_AGENT_INSTANCE_FACTORY,
    DEFAULT_ROLE_REGISTRY,
    MAX_AGENT_INSTANCE_KEY_CHARS,
    RESERVED_SPECIALIZED_ROLE_IDS,
    AgentInstance,
    AgentInstanceFactory,
    AgentInstanceKeyError,
    AgentInstanceRoleError,
    RoleDefinition,
    RoleRegistry,
    create_agent_instance,
)


def test_contract_version_and_key_bound_are_explicit() -> None:
    assert AGENT_INSTANCE_CONTRACT_VERSION == "1.0"
    assert MAX_AGENT_INSTANCE_KEY_CHARS == 128


@pytest.mark.parametrize(
    "role_id",
    (
        "coordinator",
        "requirements_engineer",
        "uav_systems_engineer",
        "reviewer",
    ),
)
def test_default_factory_creates_all_mvp_roles(role_id: str) -> None:
    instance = create_agent_instance(
        instance_key=f"instance:{role_id}",
        role_id=role_id,
    )

    assert instance.role_id == role_id
    assert instance.role is DEFAULT_ROLE_REGISTRY.get(role_id)
    assert instance.logical_identity == (
        f"{role_id}:instance:{role_id}"
    )


def test_same_role_with_distinct_keys_is_logically_isolated() -> None:
    first = create_agent_instance(
        instance_key="TASK-000001",
        role_id="uav_systems_engineer",
    )
    second = create_agent_instance(
        instance_key="TASK-000002",
        role_id="uav_systems_engineer",
    )

    assert first != second
    assert first.role is second.role
    assert first.logical_identity != second.logical_identity


def test_canonical_task_id_is_valid_future_mapping_key() -> None:
    instance = create_agent_instance(
        instance_key="TASK-000001",
        role_id="requirements_engineer",
    )

    assert instance.instance_key == "TASK-000001"


@pytest.mark.parametrize(
    "invalid",
    (
        "",
        " leading",
        "trailing ",
        "contains space",
        "../escape",
        "slash/value",
        "-starts-with-dash",
        "ümlaut",
    ),
)
def test_invalid_instance_keys_fail_closed(invalid: str) -> None:
    with pytest.raises(AgentInstanceKeyError):
        create_agent_instance(
            instance_key=invalid,
            role_id="coordinator",
        )


def test_oversized_instance_key_fails_closed() -> None:
    with pytest.raises(
        AgentInstanceKeyError,
        match="MAX_AGENT_INSTANCE_KEY_CHARS",
    ):
        create_agent_instance(
            instance_key="A" * (MAX_AGENT_INSTANCE_KEY_CHARS + 1),
            role_id="coordinator",
        )


@pytest.mark.parametrize(
    "role_id",
    RESERVED_SPECIALIZED_ROLE_IDS,
)
def test_reserved_specialized_roles_are_not_instantiable(
    role_id: str,
) -> None:
    with pytest.raises(AgentInstanceRoleError):
        create_agent_instance(
            instance_key="TASK-000001",
            role_id=role_id,
        )


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(AgentInstanceRoleError):
        create_agent_instance(
            instance_key="TASK-000001",
            role_id="invented_agent",
        )


def test_instance_is_immutable() -> None:
    instance = create_agent_instance(
        instance_key="TASK-000001",
        role_id="reviewer",
    )

    with pytest.raises(FrozenInstanceError):
        instance.instance_key = "TASK-000002"  # type: ignore[misc]


def test_direct_instance_requires_role_definition() -> None:
    with pytest.raises(AgentInstanceRoleError):
        AgentInstance(  # type: ignore[arg-type]
            instance_key="TASK-000001",
            role="coordinator",
        )


def test_factory_accepts_an_explicit_registry_without_global_mutation() -> None:
    custom_role = RoleDefinition(
        role_id="custom_test_role",
        title="Custom Test Role",
        purpose="Used only to verify injected registry isolation.",
    )
    custom_registry = RoleRegistry((custom_role,))
    factory = AgentInstanceFactory(custom_registry)

    custom = factory.create(
        instance_key="custom:001",
        role_id="custom_test_role",
    )

    assert custom.role is custom_role
    assert not DEFAULT_ROLE_REGISTRY.contains("custom_test_role")
    assert DEFAULT_AGENT_INSTANCE_FACTORY.role_registry is DEFAULT_ROLE_REGISTRY


def test_factory_is_provider_model_context_and_task_free() -> None:
    instance = create_agent_instance(
        instance_key="TASK-000001",
        role_id="coordinator",
    )

    forbidden = (
        "task",
        "task_id",
        "context",
        "prompt",
        "memory",
        "tools",
        "provider",
        "model",
        "model_manager",
        "result",
        "artifact",
        "status",
        "run_id",
    )
    for name in forbidden:
        assert not hasattr(instance, name)


def test_factory_exposes_no_generate_or_execution_api() -> None:
    factory = AgentInstanceFactory()

    for name in (
        "generate",
        "run",
        "execute",
        "submit",
        "assign_task",
        "build_context",
        "persist",
        "approve",
        "verify",
        "command_uav",
    ):
        assert not hasattr(factory, name)


def test_creating_instance_does_not_change_role_registry() -> None:
    before = DEFAULT_ROLE_REGISTRY.definitions()

    create_agent_instance(
        instance_key="TASK-000001",
        role_id="reviewer",
    )

    assert DEFAULT_ROLE_REGISTRY.definitions() == before
