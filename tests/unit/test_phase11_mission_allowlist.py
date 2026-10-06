from __future__ import annotations

from pathlib import Path

from ai.contracts.validation_result import ValidationStatus
from ai.uav_bridge import (
    BRIDGE_MISSION_ALLOWLIST,
    MISSION_ALLOWLIST_CONTRACT_VERSION,
    authorize_bridge_mission,
    parse_simulation_proposal,
)


PROJECT = Path(__file__).resolve().parents[2]


def mission_payload(*, mission_id: str = "MISSION-001", capability_id: str = "NAV-001"):
    return {
        "mission_id": mission_id,
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
                "capability_id": capability_id,
                "post_action": "hold",
                "input": {
                    "task_id": capability_id,
                    "frame": "LOCAL_NED",
                    "target_position_m": [5.0, 0.0, -3.0],
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


def proposal(*, mission_id: str = "MISSION-001", capability_id: str = "NAV-001"):
    return parse_simulation_proposal(
        {
            "project_id": "UAV-PROJ-000001",
            "requirement_version": 1,
            "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
            "scenario_id": "U11-5-TEST",
            "execution_scope": "SIMULATION_ONLY",
            "mission_id": mission_id,
            "mission_payload": mission_payload(
                mission_id=mission_id,
                capability_id=capability_id,
            ),
        }
    )


def check(result, check_id: str):
    return next(item for item in result.checks if item.check_id == check_id)


def write_policy(root: Path):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "input_policy.yaml").write_text(
        "version: 1\n"
        "status: simulation_only_configurable_workspace\n"
        "supported_frames:\n"
        "  - LOCAL_NED\n"
        "test_area_source: experiment.yaml\n",
        encoding="utf-8",
    )
    (root / "config" / "experiment.yaml").write_text(
        "permitted_test_region:\n"
        "  x_m: {min: -20.0, max: 20.0}\n"
        "  y_m: {min: -20.0, max: 20.0}\n"
        "  z_m: {min: -10.0, max: 0.0}\n",
        encoding="utf-8",
    )


def test_contract_and_allowlist_are_exact_and_explicit():
    assert MISSION_ALLOWLIST_CONTRACT_VERSION == "1.0"
    assert BRIDGE_MISSION_ALLOWLIST == ("MISSION-001",)


def test_nominal_mission_is_authorized_after_u11_4():
    result = authorize_bridge_mission(PROJECT, proposal())
    assert result.overall is ValidationStatus.PASS
    assert [item.check_id for item in result.checks] == [
        "u11_5.capability_authorization",
        "u11_5.mission_allowlist",
    ]
    allow = check(result, "u11_5.mission_allowlist")
    assert allow.status is ValidationStatus.PASS
    assert allow.details["allowed"] == ("MISSION-001",)
    assert allow.details["observed"] == "MISSION-001"
    assert allow.details["deny_by_default"] is True
    assert allow.details["authorization_source"] == "explicit_static_allowlist"


def test_unknown_mission_is_denied_by_default():
    result = authorize_bridge_mission(PROJECT, proposal(mission_id="FUTURE-MISSION-777"))
    assert result.overall is ValidationStatus.FAIL
    allow = check(result, "u11_5.mission_allowlist")
    assert allow.status is ValidationStatus.FAIL
    assert allow.details["denied"] == "FUTURE-MISSION-777"
    assert allow.details["deny_by_default"] is True


def test_u11_4_failure_prevents_mission_authorization_pass():
    result = authorize_bridge_mission(
        PROJECT,
        proposal(capability_id="FUTURE-CAP-999"),
    )
    assert result.overall is ValidationStatus.FAIL
    capability = check(result, "u11_5.capability_authorization")
    mission = check(result, "u11_5.mission_allowlist")
    assert capability.status is ValidationStatus.FAIL
    assert "u11_4.capability_allowlist" in capability.details["failed_check_ids"]
    assert mission.status is ValidationStatus.PASS


def test_mission_policy_is_not_inferred_from_runtime_support():
    # U11.5 owns its own explicit policy. The only authorization source is the
    # static allowlist, not src.mission.model or runtime registry membership.
    result = authorize_bridge_mission(PROJECT, proposal(mission_id="FUTURE-MISSION-777"))
    allow = check(result, "u11_5.mission_allowlist")
    assert allow.details["authorization_source"] == "explicit_static_allowlist"
    assert allow.status is ValidationStatus.FAIL


def test_authorization_is_read_only(tmp_path: Path):
    write_policy(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = authorize_bridge_mission(tmp_path, proposal())
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert result.overall is ValidationStatus.PASS
    assert before == after
    assert not (tmp_path / "runs").exists()


def test_wrong_input_type_is_programmer_error():
    try:
        authorize_bridge_mission(PROJECT, {})  # type: ignore[arg-type]
    except TypeError as exc:
        assert "SimulationProposal" in str(exc)
    else:
        raise AssertionError("non-SimulationProposal input must be rejected")
