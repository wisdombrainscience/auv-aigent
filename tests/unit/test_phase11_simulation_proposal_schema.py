from __future__ import annotations

import json
import math

import pytest

from ai.uav_bridge import (
    ExecutionScope,
    MAX_SIMULATION_PROPOSAL_JSON_DEPTH,
    SIMULATION_PROPOSAL_CONTRACT_VERSION,
    SimulationProposal,
    SimulationProposalError,
    parse_simulation_proposal,
)


def _payload(**overrides):
    raw = {
        "project_id": "UAV-PROJ-000001",
        "requirement_version": 3,
        "source_artifacts": [
            {"entity_id": "ART-000021", "version": 2},
            {"entity_id": "ART-000034", "version": 1},
        ],
        "scenario_id": "U11-1-SCHEMA",
        "execution_scope": "SIMULATION_ONLY",
        "mission_id": "MISSION-001",
        "mission_payload": {
            "frame": "LOCAL_NED",
            "waypoints": [
                {"north": 5.0, "east": 0.0, "altitude": 3.0},
            ],
        },
    }
    raw.update(overrides)
    return raw


def test_contract_version_and_round_trip_are_deterministic():
    assert SIMULATION_PROPOSAL_CONTRACT_VERSION == "1.0"
    proposal = parse_simulation_proposal(_payload())
    assert proposal.project_id.value == "UAV-PROJ-000001"
    assert proposal.execution_scope is ExecutionScope.SIMULATION_ONLY
    assert proposal.mission_id == "MISSION-001"
    assert proposal.to_dict() == _payload()
    assert json.loads(proposal.to_json()) == _payload()
    assert proposal.to_json() == parse_simulation_proposal(proposal.to_dict()).to_json()


def test_payload_keys_are_exact():
    raw = _payload()
    raw["verdict"] = "PASS"
    with pytest.raises(SimulationProposalError, match="exactly the U11.1 fields"):
        parse_simulation_proposal(raw)
    raw = _payload()
    del raw["mission_payload"]
    with pytest.raises(SimulationProposalError, match="exactly the U11.1 fields"):
        parse_simulation_proposal(raw)


@pytest.mark.parametrize("project_id", ["PROJECT-000001", "TASK-000001", "UAV-PROJ-000000", " UAV-PROJ-000001"])
def test_project_id_must_be_canonical_project(project_id):
    with pytest.raises(SimulationProposalError, match="PROJECT ID"):
        parse_simulation_proposal(_payload(project_id=project_id))


@pytest.mark.parametrize("value", [True, 0, -1, 1_000_000])
def test_requirement_version_is_bounded(value):
    with pytest.raises(SimulationProposalError, match="requirement_version"):
        parse_simulation_proposal(_payload(requirement_version=value))


def test_source_artifacts_require_artifact_ids_and_unique_versions():
    with pytest.raises(SimulationProposalError, match="ARTIFACT ID"):
        parse_simulation_proposal(_payload(source_artifacts=[{"entity_id": "REQ-000001", "version": 1}]))
    duplicate = [
        {"entity_id": "ART-000001", "version": 1},
        {"entity_id": "ART-000001", "version": 1},
    ]
    with pytest.raises(SimulationProposalError, match="duplicate"):
        parse_simulation_proposal(_payload(source_artifacts=duplicate))
    with pytest.raises(SimulationProposalError, match="1..64"):
        parse_simulation_proposal(_payload(source_artifacts=[]))


def test_execution_scope_is_simulation_only():
    for invalid in ("REAL_WORLD", "SIMULATION", "simulation_only", None):
        with pytest.raises(SimulationProposalError, match="SIMULATION_ONLY"):
            parse_simulation_proposal(_payload(execution_scope=invalid))


def test_u111_does_not_preempt_mission_allowlist():
    proposal = parse_simulation_proposal(_payload(mission_id="FUTURE-MISSION-777"))
    assert proposal.mission_id == "FUTURE-MISSION-777"


@pytest.mark.parametrize("mission_id", ["mission-001", "MISSION_001", " MISSION-001", "", "MISSION 001"])
def test_mission_id_has_bounded_wire_format(mission_id):
    with pytest.raises(SimulationProposalError, match="mission_id"):
        parse_simulation_proposal(_payload(mission_id=mission_id))


def test_scenario_is_clean_bounded_text():
    for invalid in ("", " X", "X ", "A\x00B", "x" * 129):
        with pytest.raises(SimulationProposalError, match="scenario_id"):
            parse_simulation_proposal(_payload(scenario_id=invalid))


def test_mission_payload_is_strict_finite_json_object():
    with pytest.raises(SimulationProposalError, match="JSON object"):
        parse_simulation_proposal(_payload(mission_payload=[1, 2, 3]))
    with pytest.raises(SimulationProposalError, match="non-finite"):
        parse_simulation_proposal(_payload(mission_payload={"x": math.inf}))
    with pytest.raises(SimulationProposalError, match="unsupported JSON"):
        parse_simulation_proposal(_payload(mission_payload={"x": {1, 2}}))
    with pytest.raises(SimulationProposalError, match="invalid object key"):
        parse_simulation_proposal(_payload(mission_payload={1: "x"}))


def test_payload_is_immutable_after_parse():
    proposal = parse_simulation_proposal(_payload())
    with pytest.raises(TypeError):
        proposal.mission_payload["frame"] = "BROKEN"  # type: ignore[index]
    waypoints = proposal.mission_payload["waypoints"]
    assert isinstance(waypoints, tuple)


def test_payload_depth_is_bounded():
    value = {"leaf": 1}
    for index in range(MAX_SIMULATION_PROPOSAL_JSON_DEPTH + 2):
        value = {f"d{index}": value}
    with pytest.raises(SimulationProposalError, match="maximum JSON depth"):
        parse_simulation_proposal(_payload(mission_payload=value))


def test_constructor_enforces_same_invariants_as_parser():
    parsed = parse_simulation_proposal(_payload())
    rebuilt = SimulationProposal(
        project_id=parsed.project_id,
        requirement_version=parsed.requirement_version,
        source_artifacts=parsed.source_artifacts,
        scenario_id=parsed.scenario_id,
        execution_scope=parsed.execution_scope,
        mission_id=parsed.mission_id,
        mission_payload=parsed.mission_payload,
    )
    assert rebuilt.to_dict() == parsed.to_dict()
