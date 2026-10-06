from __future__ import annotations

from pathlib import Path

from ai.contracts.validation_result import ValidationStatus
from ai.uav_bridge import (
    BRIDGE_VALIDATION_CONTRACT_VERSION,
    parse_simulation_proposal,
    validate_bridge_proposal,
)


PROJECT = Path(__file__).resolve().parents[2]


def mission_payload(*, north: object = 5.0, east: object = 0.0, down: object = -3.0):
    return {
        "mission_id": "MISSION-001",
        "frame": "LOCAL_NED",
        "continuity_tolerance_m": 1.0,
        "terminal_home_position_m": [0.0, 0.0],
        "terminal_home_tolerance_m": 1.0,
        "steps": [
            {
                "step_id": "takeoff",
                "capability_id": "TAKEOFF-001",
                "post_action": "hold",
                "input": {
                    "task_id": "TAKEOFF-001",
                    "frame": "LOCAL_NED",
                    "target_altitude_m": 3.0,
                    "altitude_tolerance_m": 0.5,
                    "horizontal_tolerance_m": 1.0,
                    "timeout_s": 30.0,
                },
            },
            {
                "step_id": "waypoint_01",
                "capability_id": "NAV-001",
                "post_action": "hold",
                "input": {
                    "task_id": "NAV-001",
                    "frame": "LOCAL_NED",
                    "target_position_m": [north, east, down],
                    "position_tolerance_m": 0.5,
                    "timeout_s": 30.0,
                },
            },
            {
                "step_id": "return_home",
                "capability_id": "RETURN-HOME-001",
                "post_action": "hold",
                "input": {
                    "task_id": "RETURN-HOME-001",
                    "frame": "LOCAL_NED",
                    "home_position_m": [0.0, 0.0],
                    "return_altitude_m": 3.0,
                    "horizontal_tolerance_m": 0.5,
                    "altitude_tolerance_m": 0.5,
                    "timeout_s": 30.0,
                },
            },
            {
                "step_id": "land",
                "capability_id": "LAND-001",
                "post_action": "none",
                "input": {
                    "task_id": "LAND-001",
                    "frame": "LOCAL_NED",
                    "ground_altitude_tolerance_m": 0.5,
                    "horizontal_tolerance_m": 1.0,
                    "timeout_s": 30.0,
                },
            },
        ],
    }


def proposal(*, mission_id: str = "MISSION-001", payload=None):
    return parse_simulation_proposal(
        {
            "project_id": "UAV-PROJ-000001",
            "requirement_version": 1,
            "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
            "scenario_id": "U11-3-TEST",
            "execution_scope": "SIMULATION_ONLY",
            "mission_id": mission_id,
            "mission_payload": mission_payload() if payload is None else payload,
        }
    )


def check(result, check_id: str):
    return next(item for item in result.checks if item.check_id == check_id)


def write_policy(root: Path, *, frames=("LOCAL_NED",)):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "input_policy.yaml").write_text(
        "version: 1\n"
        "status: simulation_only_configurable_workspace\n"
        "supported_frames:\n"
        + "".join(f"  - {frame}\n" for frame in frames)
        + "test_area_source: experiment.yaml\n",
        encoding="utf-8",
    )
    (root / "config" / "experiment.yaml").write_text(
        "permitted_test_region:\n"
        "  x_m: {min: -20.0, max: 20.0}\n"
        "  y_m: {min: -20.0, max: 20.0}\n"
        "  z_m: {min: -10.0, max: 0.0}\n",
        encoding="utf-8",
    )


def test_contract_and_nominal_bridge_validation_pass():
    assert BRIDGE_VALIDATION_CONTRACT_VERSION == "1.0"
    result = validate_bridge_proposal(PROJECT, proposal())
    assert result.overall is ValidationStatus.PASS
    assert [item.check_id for item in result.checks] == [
        "u11_3.execution_scope",
        "u11_3.scenario_path_segment",
        "u11_3.mission_contract",
        "u11_3.runtime_policy",
        "u11_3.permitted_region",
    ]
    assert check(result, "u11_3.permitted_region").details["targets_checked"] == 5



def test_scenario_id_must_be_one_safe_path_segment():
    unsafe = (
        "../../escaped",
        "/tmp/escaped",
        "nested/scenario",
        r"nested\scenario",
        ".",
        "..",
        "line\nbreak",
        "contains space",
    )
    for scenario_id in unsafe:
        raw = proposal().to_dict()
        raw["scenario_id"] = scenario_id
        result = validate_bridge_proposal(PROJECT, parse_simulation_proposal(raw))
        assert result.overall is ValidationStatus.FAIL
        failed = check(result, "u11_3.scenario_path_segment")
        assert failed.status is ValidationStatus.FAIL
        assert "filesystem-safe path segment" in (failed.reason or "")


def test_scenario_id_safe_segment_variants_pass():
    for scenario_id in ("MISSION-DEMO", "mission_01", "A.B-C_1"):
        raw = proposal().to_dict()
        raw["scenario_id"] = scenario_id
        result = validate_bridge_proposal(PROJECT, parse_simulation_proposal(raw))
        assert result.overall is ValidationStatus.PASS

def test_out_of_region_waypoint_fails_closed():
    result = validate_bridge_proposal(
        PROJECT,
        proposal(payload=mission_payload(north=99999.0)),
    )
    assert result.overall is ValidationStatus.FAIL
    failed = check(result, "u11_3.permitted_region")
    assert failed.status is ValidationStatus.FAIL
    assert "outside the permitted simulation region" in (failed.reason or "")
    assert failed.details["outside"][0]["target"] == "waypoint_01"


def test_proposal_and_payload_mission_id_must_match():
    result = validate_bridge_proposal(
        PROJECT,
        proposal(mission_id="FUTURE-MISSION-777"),
    )
    assert result.overall is ValidationStatus.FAIL
    failed = check(result, "u11_3.mission_contract")
    assert failed.status is ValidationStatus.FAIL
    assert "deterministic MISSION-001 bridge contract" in (failed.reason or "")


def test_mission_payload_is_strict_and_rejects_extra_step_input():
    raw = mission_payload()
    raw["steps"][1]["input"]["verdict"] = "PASS"
    result = validate_bridge_proposal(PROJECT, proposal(payload=raw))
    assert result.overall is ValidationStatus.FAIL
    assert check(result, "u11_3.mission_contract").status is ValidationStatus.FAIL


def test_mapped_sequence_requires_nav_then_return_home_then_land():
    raw = mission_payload()
    raw["steps"][1]["capability_id"] = "LAND-001"
    raw["steps"][1]["input"] = dict(raw["steps"][-1]["input"])
    raw["steps"][1]["post_action"] = "none"
    result = validate_bridge_proposal(PROJECT, proposal(payload=raw))
    assert result.overall is ValidationStatus.FAIL
    assert check(result, "u11_3.mission_contract").status is ValidationStatus.FAIL


def test_invalid_target_geometry_fails_region_validation():
    raw = mission_payload(north="not-a-number")
    result = validate_bridge_proposal(PROJECT, proposal(payload=raw))
    assert result.overall is ValidationStatus.FAIL
    assert check(result, "u11_3.permitted_region").status is ValidationStatus.FAIL


def test_runtime_policy_frame_support_is_authoritative(tmp_path: Path):
    write_policy(tmp_path, frames=("FUTURE_FRAME",))
    result = validate_bridge_proposal(tmp_path, proposal())
    assert result.overall is ValidationStatus.FAIL
    failed = check(result, "u11_3.runtime_policy")
    assert failed.status is ValidationStatus.FAIL
    assert failed.details["observed"] == "LOCAL_NED"


def test_missing_runtime_policy_fails_closed(tmp_path: Path):
    result = validate_bridge_proposal(tmp_path, proposal())
    assert result.overall is ValidationStatus.FAIL
    failed = check(result, "u11_3.runtime_policy")
    assert failed.status is ValidationStatus.FAIL
    assert "unavailable or invalid" in (failed.reason or "")


def test_validation_is_read_only_and_does_not_create_runtime_evidence(tmp_path: Path):
    write_policy(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = validate_bridge_proposal(tmp_path, proposal())
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert result.overall is ValidationStatus.PASS
    assert before == after
    assert not (tmp_path / "runs").exists()


def test_wrong_input_type_is_programmer_error():
    try:
        validate_bridge_proposal(PROJECT, {})  # type: ignore[arg-type]
    except TypeError as exc:
        assert "SimulationProposal" in str(exc)
    else:
        raise AssertionError("non-SimulationProposal input must be rejected")
