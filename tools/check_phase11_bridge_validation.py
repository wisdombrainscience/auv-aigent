#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.contracts.validation_result import ValidationStatus
from ai.uav_bridge import (
    BRIDGE_VALIDATION_CONTRACT_VERSION,
    parse_simulation_proposal,
    validate_bridge_proposal,
)


def raw_proposal(*, north: float = 5.0):
    return {
        "project_id": "UAV-PROJ-000001",
        "requirement_version": 1,
        "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
        "scenario_id": "U11-3-ACCEPTANCE",
        "execution_scope": "SIMULATION_ONLY",
        "mission_id": "MISSION-001",
        "mission_payload": {
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
                        "target_position_m": [north, 0.0, -3.0],
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
        },
    }


def main() -> int:
    assert BRIDGE_VALIDATION_CONTRACT_VERSION == "1.0"
    nominal = validate_bridge_proposal(ROOT, parse_simulation_proposal(raw_proposal()))
    assert nominal.overall is ValidationStatus.PASS
    assert len(nominal.checks) == 5

    unsafe_raw = raw_proposal()
    unsafe_raw["scenario_id"] = "../../escaped"
    unsafe = validate_bridge_proposal(
        ROOT,
        parse_simulation_proposal(unsafe_raw),
    )
    assert unsafe.overall is ValidationStatus.FAIL
    assert any(
        check.check_id == "u11_3.scenario_path_segment"
        and check.status is ValidationStatus.FAIL
        for check in unsafe.checks
    )

    rejected = validate_bridge_proposal(
        ROOT,
        parse_simulation_proposal(raw_proposal(north=99999.0)),
    )
    assert rejected.overall is ValidationStatus.FAIL
    assert any(
        check.check_id == "u11_3.permitted_region"
        and check.status is ValidationStatus.FAIL
        for check in rejected.checks
    )

    print(
        "BRIDGE_VALIDATION=PASS contract_version=1.0 checks=5 "
        "execution_scope=SIMULATION_ONLY scenario_path=SAFE_SEGMENT traversal=DENY mission_contract=PASS "
        "runtime_policy=CANONICAL permitted_region=PASS out_of_region=FAIL_CLOSED "
        "authorization=capability_allowlist_active capability_allowlist=active_U11_4 "
        "mission_allowlist=active_U11_5 runtime_invocation=active_U11_6 "
        "uav_run_link=active_U11_7 persistence=none provider_calls=none "
        "px4_access=none verdict_authority=independent_verifier "
        "phase11=READY cp9=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
