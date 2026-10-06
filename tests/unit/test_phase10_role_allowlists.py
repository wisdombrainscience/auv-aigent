from dataclasses import FrozenInstanceError, fields

import pytest

from ai.agents.roles import DEFAULT_ROLE_REGISTRY
from ai.tools import (
    DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    DEFAULT_TOOL_REGISTRY,
    FORBIDDEN_BY_DEFAULT_TOOL_IDS,
    ROLE_TOOL_ALLOWLIST_CONTRACT_VERSION,
    RoleToolAllowlist,
    RoleToolAllowlistError,
    RoleToolAllowlistRegistry,
    ToolNotAllowedError,
    get_default_role_tool_allowlist_registry,
)


EXPECTED = {
    "coordinator": (),
    "requirements_engineer": ("artifact_read", "draft_write", "project_read"),
    "reviewer": ("artifact_read", "issue_create", "project_read"),
    "uav_systems_engineer": (
        "artifact_read",
        "calculator",
        "draft_write",
        "project_read",
    ),
}


def test_contract_version_is_frozen() -> None:
    assert ROLE_TOOL_ALLOWLIST_CONTRACT_VERSION == "1.0"


def test_wire_fields_are_minimal() -> None:
    assert tuple(field.name for field in fields(RoleToolAllowlist)) == (
        "role_id",
        "tool_ids",
    )


def test_default_registry_covers_exact_frozen_mvp_roles() -> None:
    assert set(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.role_ids()) == set(
        DEFAULT_ROLE_REGISTRY.role_ids()
    )
    assert len(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.entries()) == 4


@pytest.mark.parametrize("role_id,expected", tuple(EXPECTED.items()))
def test_default_allowlists_match_frozen_section_22(role_id: str, expected: tuple[str, ...]) -> None:
    assert DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids(role_id) == expected


def test_coordinator_is_least_privilege_empty() -> None:
    assert DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids("coordinator") == ()


def test_requirements_role_has_exact_frozen_example() -> None:
    assert set(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids("requirements_engineer")) == {
        "project_read", "artifact_read", "draft_write"
    }


def test_engineering_role_has_exact_frozen_example() -> None:
    assert set(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids("uav_systems_engineer")) == {
        "project_read", "calculator", "artifact_read", "draft_write"
    }


def test_reviewer_role_has_exact_frozen_example() -> None:
    assert set(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids("reviewer")) == {
        "artifact_read", "project_read", "issue_create"
    }


@pytest.mark.parametrize(
    "role_id,tool_id",
    [
        ("requirements_engineer", "project_read"),
        ("requirements_engineer", "artifact_read"),
        ("requirements_engineer", "draft_write"),
        ("uav_systems_engineer", "calculator"),
        ("reviewer", "issue_create"),
    ],
)
def test_known_allowed_pairs_pass(role_id: str, tool_id: str) -> None:
    assert DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.is_allowed(role_id, tool_id)
    assert DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.require_allowed(role_id, tool_id) == tool_id


@pytest.mark.parametrize(
    "role_id,tool_id",
    [
        ("coordinator", "project_read"),
        ("requirements_engineer", "calculator"),
        ("requirements_engineer", "issue_create"),
        ("uav_systems_engineer", "issue_create"),
        ("reviewer", "draft_write"),
        ("reviewer", "calculator"),
        ("reviewer", "workspace_write"),
    ],
)
def test_known_but_not_allowlisted_pairs_are_denied(role_id: str, tool_id: str) -> None:
    assert not DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.is_allowed(role_id, tool_id)
    with pytest.raises(ToolNotAllowedError):
        DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.require_allowed(role_id, tool_id)


def test_reserved_software_role_is_not_activated() -> None:
    assert not DEFAULT_ROLE_REGISTRY.contains("software_engineer")
    assert not DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.is_allowed(
        "software_engineer", "workspace_read"
    )
    with pytest.raises(RoleToolAllowlistError, match="role is not registered"):
        DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.require_allowed(
            "software_engineer", "workspace_read"
        )


@pytest.mark.parametrize("tool_id", FORBIDDEN_BY_DEFAULT_TOOL_IDS)
def test_forbidden_capabilities_cannot_be_allowed(tool_id: str) -> None:
    assert not DEFAULT_TOOL_REGISTRY.contains(tool_id)
    for role_id in DEFAULT_ROLE_REGISTRY.role_ids():
        assert not DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.is_allowed(role_id, tool_id)


def test_unknown_tool_fails_closed() -> None:
    assert not DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.is_allowed("reviewer", "future_tool")
    with pytest.raises(RoleToolAllowlistError, match="tool is not registered"):
        DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.require_allowed("reviewer", "future_tool")


def test_unknown_role_fails_closed() -> None:
    assert not DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.is_allowed("future_role", "project_read")
    with pytest.raises(RoleToolAllowlistError, match="role is not registered"):
        DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.require_allowed("future_role", "project_read")


def test_entry_is_frozen() -> None:
    entry = DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.get("reviewer")
    with pytest.raises(FrozenInstanceError):
        entry.role_id = "coordinator"  # type: ignore[misc]


def test_singleton_accessor() -> None:
    assert get_default_role_tool_allowlist_registry() is DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY


def test_registry_order_is_deterministic() -> None:
    assert DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.role_ids() == tuple(
        sorted(DEFAULT_ROLE_REGISTRY.role_ids())
    )
    for entry in DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.entries():
        assert entry.tool_ids == tuple(sorted(entry.tool_ids))


def test_duplicate_role_entries_rejected() -> None:
    with pytest.raises(RoleToolAllowlistError, match="duplicate role_id"):
        RoleToolAllowlistRegistry(
            (
                RoleToolAllowlist("coordinator", ()),
                RoleToolAllowlist("coordinator", ()),
                RoleToolAllowlist("requirements_engineer", ()),
                RoleToolAllowlist("reviewer", ()),
                RoleToolAllowlist("uav_systems_engineer", ()),
            )
        )


def test_missing_role_entry_rejected() -> None:
    with pytest.raises(RoleToolAllowlistError, match="cover registered roles exactly"):
        RoleToolAllowlistRegistry(
            (
                RoleToolAllowlist("coordinator", ()),
                RoleToolAllowlist("requirements_engineer", ()),
                RoleToolAllowlist("reviewer", ()),
            )
        )


def test_unknown_role_entry_rejected() -> None:
    with pytest.raises(RoleToolAllowlistError, match="allowlist role is not registered"):
        RoleToolAllowlistRegistry(
            (
                RoleToolAllowlist("coordinator", ()),
                RoleToolAllowlist("requirements_engineer", ()),
                RoleToolAllowlist("reviewer", ()),
                RoleToolAllowlist("uav_systems_engineer", ()),
                RoleToolAllowlist("software_engineer", ()),
            )
        )


def test_unknown_tool_entry_rejected() -> None:
    with pytest.raises(RoleToolAllowlistError, match="allowlist tool is not registered"):
        RoleToolAllowlistRegistry(
            (
                RoleToolAllowlist("coordinator", ()),
                RoleToolAllowlist("requirements_engineer", ("future_tool",)),
                RoleToolAllowlist("reviewer", ()),
                RoleToolAllowlist("uav_systems_engineer", ()),
            )
        )


def test_duplicate_tool_in_entry_rejected() -> None:
    with pytest.raises(RoleToolAllowlistError, match="duplicate tool_id"):
        RoleToolAllowlist("reviewer", ("project_read", "project_read"))


def test_unsorted_tools_rejected() -> None:
    with pytest.raises(RoleToolAllowlistError, match="sorted deterministically"):
        RoleToolAllowlist("reviewer", ("project_read", "artifact_read"))


def test_x10_2_contains_no_execution_or_later_gate_fields() -> None:
    entry_fields = {field.name for field in fields(RoleToolAllowlist)}
    assert "execute" not in entry_fields
    assert "callable" not in entry_fields
    assert "timeout" not in entry_fields
    assert "path" not in entry_fields
    assert "result_schema" not in entry_fields
    assert "audit" not in entry_fields
    assert not hasattr(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY, "execute")
