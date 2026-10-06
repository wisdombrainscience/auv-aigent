from dataclasses import FrozenInstanceError, fields

import pytest

from ai.tools import (
    DEFAULT_TOOL_IDS,
    DEFAULT_TOOL_REGISTRY,
    FORBIDDEN_BY_DEFAULT_TOOL_IDS,
    MAX_REGISTERED_TOOLS,
    MAX_TOOL_PURPOSE_BYTES,
    TOOL_REGISTRY_CONTRACT_VERSION,
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
    UnknownToolError,
    get_default_tool_registry,
)


def _tool(tool_id: str = "sample_tool") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        title="Sample Tool",
        purpose="Bounded test-only tool metadata.",
    )


def test_contract_version_is_frozen() -> None:
    assert TOOL_REGISTRY_CONTRACT_VERSION == "1.0"


def test_tool_definition_wire_metadata_is_minimal() -> None:
    assert tuple(field.name for field in fields(ToolDefinition)) == (
        "tool_id",
        "title",
        "purpose",
    )


def test_default_tool_ids_match_frozen_section_22_examples() -> None:
    assert DEFAULT_TOOL_IDS == (
        "project_read",
        "artifact_read",
        "draft_write",
        "calculator",
        "workspace_read",
        "workspace_write",
        "allowlisted_test_runner",
        "issue_create",
    )


def test_default_registry_contains_exactly_eight_tools() -> None:
    assert len(DEFAULT_TOOL_REGISTRY.definitions()) == 8
    assert set(DEFAULT_TOOL_REGISTRY.tool_ids()) == set(DEFAULT_TOOL_IDS)


def test_registry_order_is_deterministic_sorted_by_id() -> None:
    assert DEFAULT_TOOL_REGISTRY.tool_ids() == tuple(sorted(DEFAULT_TOOL_IDS))


@pytest.mark.parametrize("tool_id", DEFAULT_TOOL_IDS)
def test_each_default_tool_is_known(tool_id: str) -> None:
    assert DEFAULT_TOOL_REGISTRY.contains(tool_id)
    assert DEFAULT_TOOL_REGISTRY.require_known(tool_id) == tool_id
    assert DEFAULT_TOOL_REGISTRY.get(tool_id).tool_id == tool_id


@pytest.mark.parametrize("tool_id", FORBIDDEN_BY_DEFAULT_TOOL_IDS)
def test_forbidden_capabilities_are_not_registered(tool_id: str) -> None:
    assert not DEFAULT_TOOL_REGISTRY.contains(tool_id)
    with pytest.raises(UnknownToolError):
        DEFAULT_TOOL_REGISTRY.get(tool_id)


def test_forbidden_set_matches_frozen_default_denials() -> None:
    assert FORBIDDEN_BY_DEFAULT_TOOL_IDS == (
        "unrestricted_shell",
        "arbitrary_filesystem_path",
        "arbitrary_network_access",
        "direct_px4_adapter",
        "direct_raw_mavsdk_command",
    )


def test_definition_is_immutable() -> None:
    item = _tool()
    with pytest.raises(FrozenInstanceError):
        item.title = "Changed"  # type: ignore[misc]


def test_default_registry_singleton_accessor() -> None:
    assert get_default_tool_registry() is DEFAULT_TOOL_REGISTRY


def test_registry_rejects_duplicate_ids() -> None:
    with pytest.raises(ToolRegistryError, match="duplicate tool_id"):
        ToolRegistry((_tool("sample"), _tool("sample")))


def test_registry_rejects_empty_registry() -> None:
    with pytest.raises(ToolRegistryError, match="must not be empty"):
        ToolRegistry(())


def test_registry_rejects_non_definition_entries() -> None:
    with pytest.raises(ToolRegistryError, match="ToolDefinition"):
        ToolRegistry((_tool("sample"), "not-a-tool"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_id",
    ("", "ProjectRead", "project-read", "project.read", "1tool", "tool space", "_tool"),
)
def test_definition_rejects_invalid_tool_ids(bad_id: str) -> None:
    with pytest.raises(ToolRegistryError, match="tool_id"):
        ToolDefinition(
            tool_id=bad_id,
            title="Bad",
            purpose="Invalid identifier fixture.",
        )


def test_definition_rejects_dirty_title() -> None:
    with pytest.raises(ToolRegistryError, match="title"):
        ToolDefinition(
            tool_id="sample_tool",
            title=" Dirty ",
            purpose="Test purpose.",
        )


def test_definition_rejects_nul_in_purpose() -> None:
    with pytest.raises(ToolRegistryError, match="purpose"):
        ToolDefinition(
            tool_id="sample_tool",
            title="Sample",
            purpose="bad\x00purpose",
        )


def test_definition_rejects_oversize_purpose() -> None:
    with pytest.raises(ToolRegistryError, match="MAX_TOOL_PURPOSE_BYTES"):
        ToolDefinition(
            tool_id="sample_tool",
            title="Sample",
            purpose="x" * (MAX_TOOL_PURPOSE_BYTES + 1),
        )


def test_registry_has_defensive_count_bound() -> None:
    tools = tuple(
        ToolDefinition(
            tool_id=f"tool_{index}",
            title=f"Tool {index}",
            purpose="Bounded fixture.",
        )
        for index in range(MAX_REGISTERED_TOOLS + 1)
    )
    with pytest.raises(ToolRegistryError, match="MAX_REGISTERED_TOOLS"):
        ToolRegistry(tools)


def test_x10_1_has_no_role_permission_or_executor_fields() -> None:
    definition_fields = {field.name for field in fields(ToolDefinition)}
    assert "roles" not in definition_fields
    assert "allowlist" not in definition_fields
    assert "callable" not in definition_fields
    assert "executor" not in definition_fields
    assert "timeout" not in definition_fields
    assert "path" not in definition_fields
    assert "result_schema" not in definition_fields
    assert "audit" not in definition_fields


def test_default_definitions_do_not_expose_callable_state() -> None:
    for definition in DEFAULT_TOOL_REGISTRY.definitions():
        assert not hasattr(definition, "execute")
        assert not hasattr(definition, "callable")
        assert not hasattr(definition, "command")


def test_unknown_valid_id_fails_closed() -> None:
    assert not DEFAULT_TOOL_REGISTRY.contains("future_tool")
    with pytest.raises(UnknownToolError):
        DEFAULT_TOOL_REGISTRY.require_known("future_tool")
