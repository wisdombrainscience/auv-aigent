from __future__ import annotations

import pytest

from ai.agents.roles import RoleDefinition, RoleRegistry
from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.simulation_feedback.parser import MISSION_VERIFIER_CHECK_IDS, ParsedVerifierResult
from ai.simulation_feedback.routing import (
    SIMULATION_ISSUE_ROLE_MAPPING_CONTRACT_VERSION,
    UAV_SYSTEMS_ENGINEER_ROLE_ID,
    SimulationIssueRoleMappingError,
    issue_role_map,
    route_simulation_issues,
)
from ai.simulation_feedback.taxonomy import SimulationIssueType, classify_simulation_issues


def _issue_set(*failed: str):
    failed_set = set(failed)
    checks = {
        check_id: ("FAIL" if check_id in failed_set else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    parsed = ParsedVerifierResult(
        project_id=CanonicalId.from_sequence(IdKind.PROJECT, 1),
        requirement_version=4,
        source_artifacts=(
            ArtifactInputVersion(
                entity_id=CanonicalId.from_sequence(IdKind.ARTIFACT, 9),
                version=3,
            ),
        ),
        runtime_run_id="MISSION001-F12-3-RUN",
        mission_id="MISSION-001",
        scenario_id="F12-3-ROUTING",
        overall="FAIL" if failed_set else "PASS",
        checks=checks,
        failed_checks=tuple(
            check_id for check_id in MISSION_VERIFIER_CHECK_IDS if check_id in failed_set
        ),
        failed_step_ids=("nav-1",) if failed_set else (),
        max_transition_distance_m=1.5 if "continuity" in failed_set else 0.0,
        terminal_home_distance_m=2.5 if "terminal_home" in failed_set else 0.1,
        reason="verification failed" if failed_set else None,
        verification_sha256="c" * 64,
    )
    return classify_simulation_issues(parsed)


def test_contract_and_explicit_mapping_are_frozen():
    assert SIMULATION_ISSUE_ROLE_MAPPING_CONTRACT_VERSION == "1.0"
    assert UAV_SYSTEMS_ENGINEER_ROLE_ID == "uav_systems_engineer"
    mapping = issue_role_map()
    assert set(mapping) == set(SimulationIssueType)
    assert set(mapping.values()) == {UAV_SYSTEMS_ENGINEER_ROLE_ID}


def test_all_seven_issue_types_route_to_active_uav_systems_engineer():
    plan = route_simulation_issues(_issue_set(*MISSION_VERIFIER_CHECK_IDS))
    assert len(plan.routes) == 7
    assert {route.owner_role for route in plan.routes} == {UAV_SYSTEMS_ENGINEER_ROLE_ID}
    assert tuple(route.issue.check_id for route in plan.routes) == MISSION_VERIFIER_CHECK_IDS


def test_pass_issue_set_routes_to_empty_plan_and_preserves_lineage():
    issue_set = _issue_set()
    plan = route_simulation_issues(issue_set)
    assert plan.routes == ()
    assert plan.project_id == issue_set.project_id
    assert plan.requirement_version == issue_set.requirement_version
    assert plan.source_artifacts == issue_set.source_artifacts
    assert plan.runtime_run_id == issue_set.runtime_run_id
    assert plan.verification_sha256 == issue_set.verification_sha256
    assert plan.provenance is ProvenanceClass.VERIFIER_RESULT


def test_route_preserves_exact_issue_fingerprint_and_failure():
    issue_set = _issue_set("continuity")
    route = route_simulation_issues(issue_set).routes[0]
    issue = issue_set.issues[0]
    assert route.issue is issue
    assert route.issue.fingerprint == issue.fingerprint
    assert route.issue.failure == issue.failure
    assert route.owner_role == UAV_SYSTEMS_ENGINEER_ROLE_ID


def test_reserved_specialized_roles_are_not_activated_by_mapping():
    serialized = repr(route_simulation_issues(_issue_set("terminal_home")).to_dict())
    assert "simulation_engineer" not in serialized
    assert "verification_engineer" not in serialized
    assert "px4_flight_engineer" not in serialized
    assert "software_engineer" not in serialized
    assert "reviewer" not in serialized


def test_missing_active_owner_role_fails_closed():
    registry = RoleRegistry(
        (
            RoleDefinition("coordinator", "Coordinator", "Coordinates bounded work."),
            RoleDefinition("reviewer", "Reviewer", "Advisory semantic review only."),
        )
    )
    with pytest.raises(SimulationIssueRoleMappingError, match="not active"):
        route_simulation_issues(_issue_set("continuity"), role_registry=registry)


def test_wrong_input_type_fails_closed():
    with pytest.raises(TypeError, match="SimulationIssueSet"):
        route_simulation_issues({})  # type: ignore[arg-type]


def test_serialized_plan_has_no_issue_id_persistence_task_or_retry_fields():
    raw = route_simulation_issues(_issue_set("terminal_landed_state")).to_dict()
    encoded = repr(raw)
    assert "issue_id" not in encoded
    assert "task_id" not in encoded
    assert "rework_task" not in encoded
    assert "retry_attempt" not in encoded
    assert "persisted" not in encoded
