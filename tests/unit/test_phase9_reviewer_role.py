from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import subprocess
import sys

import pytest

from ai.agents import DEFAULT_ROLE_REGISTRY, MVP_ROLE_IDS, RoleDefinition, RoleRegistry
from ai.contracts import REVIEW_RESULT_CONTRACT_VERSION, ReviewResult
from ai.review import (
    DEFAULT_REVIEWER_ROLE,
    REVIEWER_OUTPUT_CONTRACT,
    REVIEWER_ROLE_CONTRACT_VERSION,
    REVIEWER_ROLE_ID,
    REVIEWER_ROLE_TITLE,
    ReviewerRoleError,
    ReviewerRoleProfile,
    bind_reviewer_role,
    create_reviewer_agent,
)


def test_contract_constants_are_explicit() -> None:
    assert REVIEWER_ROLE_CONTRACT_VERSION == "1.0"
    assert REVIEWER_ROLE_ID == "reviewer"
    assert REVIEWER_ROLE_TITLE == "Reviewer"
    assert REVIEWER_OUTPUT_CONTRACT == "ReviewResult"


def test_default_profile_binds_existing_reviewer_role() -> None:
    assert DEFAULT_REVIEWER_ROLE.role is DEFAULT_ROLE_REGISTRY.get("reviewer")
    assert DEFAULT_REVIEWER_ROLE.role_id == "reviewer"
    assert DEFAULT_REVIEWER_ROLE.role.title == "Reviewer"
    assert DEFAULT_REVIEWER_ROLE.output_contract == "ReviewResult"


def test_r9_2_does_not_add_new_role_id() -> None:
    assert MVP_ROLE_IDS == (
        "coordinator",
        "requirements_engineer",
        "uav_systems_engineer",
        "reviewer",
    )
    assert DEFAULT_ROLE_REGISTRY.role_ids() == (
        "coordinator",
        "requirements_engineer",
        "reviewer",
        "uav_systems_engineer",
    )
    assert len(DEFAULT_ROLE_REGISTRY.definitions()) == 4


def test_profile_uses_frozen_review_result_contract_version() -> None:
    assert DEFAULT_REVIEWER_ROLE.result_contract_version == REVIEW_RESULT_CONTRACT_VERSION


def test_require_result_accepts_review_result_without_mutation() -> None:
    result = ReviewResult(issues=())
    assert DEFAULT_REVIEWER_ROLE.require_result(result) is result


@pytest.mark.parametrize("invalid", [None, {}, [], "review", 1, object()])
def test_require_result_rejects_non_review_result(invalid: object) -> None:
    with pytest.raises(ReviewerRoleError, match="ReviewResult"):
        DEFAULT_REVIEWER_ROLE.require_result(invalid)  # type: ignore[arg-type]


def test_profile_rejects_non_role_definition() -> None:
    with pytest.raises(ReviewerRoleError, match="RoleDefinition"):
        ReviewerRoleProfile(role="reviewer")  # type: ignore[arg-type]


def test_profile_rejects_non_reviewer_role() -> None:
    with pytest.raises(ReviewerRoleError, match="reviewer"):
        ReviewerRoleProfile(role=DEFAULT_ROLE_REGISTRY.get("coordinator"))


def test_profile_rejects_wrong_result_contract_version() -> None:
    with pytest.raises(ReviewerRoleError, match="result_contract_version"):
        ReviewerRoleProfile(
            role=DEFAULT_ROLE_REGISTRY.get("reviewer"),
            result_contract_version="999.0",
        )


def test_bind_rejects_non_registry() -> None:
    with pytest.raises(TypeError, match="RoleRegistry"):
        bind_reviewer_role({})  # type: ignore[arg-type]


def test_bind_fails_closed_if_reviewer_is_missing() -> None:
    registry = RoleRegistry((
        RoleDefinition(
            role_id="coordinator",
            title="Coordinator",
            purpose="Coordinates bounded work.",
        ),
    ))
    with pytest.raises(ReviewerRoleError, match="reviewer"):
        bind_reviewer_role(registry)


def test_bind_accepts_injected_registry_with_reviewer() -> None:
    role = RoleDefinition(
        role_id="reviewer",
        title="Reviewer",
        purpose="Provides bounded advisory review.",
    )
    registry = RoleRegistry((role,))
    profile = bind_reviewer_role(registry)
    assert profile.role is role


@pytest.mark.parametrize("key", ["REVIEW-000001", "TASK-000001", "review:ART-000001:v1"])
def test_create_reviewer_agent_uses_existing_agent_instance_contract(key: str) -> None:
    agent = create_reviewer_agent(instance_key=key)
    assert agent.role_id == "reviewer"
    assert agent.role is DEFAULT_ROLE_REGISTRY.get("reviewer")
    assert agent.instance_key == key


def test_agent_creation_does_not_mutate_registry() -> None:
    before = DEFAULT_ROLE_REGISTRY.definitions()
    create_reviewer_agent(instance_key="REVIEW-000001")
    assert DEFAULT_ROLE_REGISTRY.definitions() == before


def test_profile_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_REVIEWER_ROLE.role = DEFAULT_ROLE_REGISTRY.get("coordinator")  # type: ignore[misc]


def test_profile_has_no_review_workflow_or_mutation_authority() -> None:
    forbidden = (
        "allocate_issue", "persist_issue", "create_review_task", "route_issue",
        "create_rework_task", "suppress_duplicate", "approve", "reject",
        "mutate_artifact", "patch_artifact", "transition_artifact",
    )
    for name in forbidden:
        assert not hasattr(DEFAULT_REVIEWER_ROLE, name)


def test_profile_has_no_provider_or_uav_authority() -> None:
    forbidden = (
        "generate", "invoke", "provider", "model", "command_uav", "mavsdk",
        "px4", "gazebo", "verify_simulation",
    )
    for name in forbidden:
        assert not hasattr(DEFAULT_REVIEWER_ROLE, name)


def test_role_module_imports_no_provider_persistence_or_uav_runtime() -> None:
    import ai.review.role as role_module
    source = inspect.getsource(role_module)
    import_lines = tuple(
        line.strip() for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines).lower()
    for forbidden in (
        "ai.providers", "ai.project", "ai.tasks.store", "ai.artifacts",
        "sqlite", "mavsdk", "px4", "gazebo",
    ):
        assert forbidden not in joined


def test_create_agent_has_no_runtime_state() -> None:
    agent = create_reviewer_agent(instance_key="REVIEW-000001")
    for name in (
        "task", "task_id", "context", "prompt", "memory", "tools", "provider",
        "model", "result", "artifact", "status", "run_id", "issues",
    ):
        assert not hasattr(agent, name)


def test_r9_2_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_reviewer_role.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "REVIEWER_ROLE=PASS" in proc.stdout
