from __future__ import annotations

import inspect

import pytest

from ai.agents import (
    DEFAULT_ROLE_REGISTRY,
    MAX_REGISTERED_ROLES,
    MVP_ROLE_IDS,
    RESERVED_SPECIALIZED_ROLE_IDS,
    ROLE_REGISTRY_CONTRACT_VERSION,
    RoleDefinition,
    RoleRegistry,
    RoleRegistryError,
    UnknownRoleError,
    get_default_role_registry,
)


def test_contract_version_and_mvp_ids_are_frozen() -> None:
    assert ROLE_REGISTRY_CONTRACT_VERSION == "1.0"
    assert MVP_ROLE_IDS == (
        "coordinator",
        "requirements_engineer",
        "uav_systems_engineer",
        "reviewer",
    )


def test_specialized_roles_are_reserved_but_not_active() -> None:
    assert RESERVED_SPECIALIZED_ROLE_IDS == (
        "propulsion_power_engineer",
        "airframe_engineer",
        "px4_flight_engineer",
        "software_engineer",
        "simulation_engineer",
        "verification_engineer",
    )
    registry = get_default_role_registry()
    for role_id in RESERVED_SPECIALIZED_ROLE_IDS:
        assert registry.contains(role_id) is False
        with pytest.raises(UnknownRoleError):
            registry.get(role_id)


def test_default_registry_contains_only_four_mvp_roles() -> None:
    registry = get_default_role_registry()
    assert registry.role_ids() == (
        "coordinator",
        "requirements_engineer",
        "reviewer",
        "uav_systems_engineer",
    )
    assert len(registry.definitions()) == 4
    assert DEFAULT_ROLE_REGISTRY is registry


def test_role_metadata_is_prompt_free_and_bounded() -> None:
    for role in DEFAULT_ROLE_REGISTRY.definitions():
        assert isinstance(role, RoleDefinition)
        assert role.role_id
        assert role.title
        assert role.purpose
        assert not hasattr(role, "system_prompt")
        assert not hasattr(role, "prompt")
        assert not hasattr(role, "model")
        assert not hasattr(role, "tools")
        assert not hasattr(role, "memory")


def test_require_known_returns_canonical_role_id() -> None:
    assert (
        DEFAULT_ROLE_REGISTRY.require_known("uav_systems_engineer")
        == "uav_systems_engineer"
    )


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(UnknownRoleError, match="not registered"):
        DEFAULT_ROLE_REGISTRY.require_known("unknown_engineer")


def test_invalid_role_id_fails_closed() -> None:
    with pytest.raises(RoleRegistryError):
        DEFAULT_ROLE_REGISTRY.get("UAV Systems Engineer")
    assert DEFAULT_ROLE_REGISTRY.contains("UAV Systems Engineer") is False


def test_duplicate_role_ids_are_rejected() -> None:
    role = RoleDefinition(
        role_id="coordinator",
        title="Coordinator",
        purpose="Coordinates engineering work.",
    )
    with pytest.raises(RoleRegistryError, match="duplicate role_id"):
        RoleRegistry((role, role))


def test_registry_size_is_bounded() -> None:
    roles = tuple(
        RoleDefinition(
            role_id=f"role_{index}",
            title=f"Role {index}",
            purpose="Bounded role purpose.",
        )
        for index in range(MAX_REGISTERED_ROLES + 1)
    )
    with pytest.raises(RoleRegistryError, match="MAX_REGISTERED_ROLES"):
        RoleRegistry(roles)


def test_role_definition_is_immutable() -> None:
    role = DEFAULT_ROLE_REGISTRY.get("reviewer")
    with pytest.raises(Exception):
        role.role_id = "coordinator"  # type: ignore[misc]


def test_registry_api_has_no_provider_task_mapping_or_execution_methods() -> None:
    registry = DEFAULT_ROLE_REGISTRY
    forbidden = (
        "generate",
        "invoke",
        "run_agent",
        "assign_task",
        "map_task",
        "build_context",
        "persist_run",
        "create_artifact",
        "approve",
        "review",
    )
    for name in forbidden:
        assert not hasattr(registry, name)


def test_role_registry_module_does_not_import_provider_or_uav_runtime() -> None:
    import ai.agents.roles as roles_module

    source = inspect.getsource(roles_module)
    import_lines = tuple(
        line.strip()
        for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines).lower()
    assert "ai.providers" not in joined
    assert "mavsdk" not in joined
    assert "px4" not in joined
    assert "gazebo" not in joined
