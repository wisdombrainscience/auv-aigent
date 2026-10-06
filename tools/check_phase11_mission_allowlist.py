#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.contracts.validation_result import ValidationStatus
from ai.uav_bridge import (
    BRIDGE_MISSION_ALLOWLIST,
    MISSION_ALLOWLIST_CONTRACT_VERSION,
    authorize_bridge_mission,
    parse_simulation_proposal,
)


def raw_proposal(
    *,
    mission_id: str = "MISSION-001",
    capability_id: str = "NAV-001",
):
    return {
        "project_id": "UAV-PROJ-000001",
        "requirement_version": 1,
        "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
        "scenario_id": "U11-5-ACCEPTANCE",
        "execution_scope": "SIMULATION_ONLY",
        "mission_id": mission_id,
        "mission_payload": {
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
        },
    }


def main() -> int:
    assert MISSION_ALLOWLIST_CONTRACT_VERSION == "1.0"
    assert BRIDGE_MISSION_ALLOWLIST == ("MISSION-001",)

    nominal = authorize_bridge_mission(
        ROOT,
        parse_simulation_proposal(raw_proposal()),
    )
    assert nominal.overall is ValidationStatus.PASS
    assert len(nominal.checks) == 2

    denied = authorize_bridge_mission(
        ROOT,
        parse_simulation_proposal(
            raw_proposal(mission_id="FUTURE-MISSION-777")
        ),
    )
    assert denied.overall is ValidationStatus.FAIL
    mission = next(
        item for item in denied.checks
        if item.check_id == "u11_5.mission_allowlist"
    )
    assert mission.status is ValidationStatus.FAIL
    assert mission.details["denied"] == "FUTURE-MISSION-777"
    assert mission.details["deny_by_default"] is True

    upstream_denied = authorize_bridge_mission(
        ROOT,
        parse_simulation_proposal(
            raw_proposal(capability_id="FUTURE-CAP-999")
        ),
    )
    assert upstream_denied.overall is ValidationStatus.FAIL
    upstream = next(
        item for item in upstream_denied.checks
        if item.check_id == "u11_5.capability_authorization"
    )
    assert upstream.status is ValidationStatus.FAIL

    print(
        "MISSION_ALLOWLIST=PASS contract_version=1.0 allowed=1 "
        "policy=EXPLICIT_STATIC deny_by_default=PASS "
        "mission_001=ALLOW unknown_mission=DENY "
        "capability_authorization=REQUIRED_U11_4 "
        "runtime_invocation=reserved_for_U11_6 "
        "uav_run_link=reserved_for_U11_7 persistence=none provider_calls=none "
        "px4_access=none verdict_authority=independent_verifier "
        "phase11=pending cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
