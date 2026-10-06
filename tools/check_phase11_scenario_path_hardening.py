#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.contracts.validation_result import ValidationStatus
from ai.uav_bridge import (
    BridgeRuntimeInvocationDeniedError,
    invoke_existing_runtime,
    parse_simulation_proposal,
    validate_bridge_proposal,
)
from orchestration.mission_experiment import MissionExperimentResult


def raw_proposal(scenario_id: str) -> dict[str, Any]:
    return {
        "project_id": "UAV-PROJ-000001",
        "requirement_version": 1,
        "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
        "scenario_id": scenario_id,
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


class NeverRunner:
    constructed = 0

    def __init__(self, project: Path) -> None:
        type(self).constructed += 1

    async def run(self, **kwargs: Any) -> MissionExperimentResult:
        raise AssertionError("unsafe scenario must never reach runtime")


def main() -> int:
    safe = parse_simulation_proposal(raw_proposal("U11-3-H1-SAFE_01"))
    assert validate_bridge_proposal(ROOT, safe).overall is ValidationStatus.PASS

    for raw_id in ("../../escaped", "/tmp/escaped", "nested/scenario", r"nested\scenario", ".", ".."):
        proposal = parse_simulation_proposal(raw_proposal(raw_id))
        result = validate_bridge_proposal(ROOT, proposal)
        assert result.overall is ValidationStatus.FAIL
        assert any(
            c.check_id == "u11_3.scenario_path_segment"
            and c.status is ValidationStatus.FAIL
            for c in result.checks
        )

    NeverRunner.constructed = 0
    traversal = parse_simulation_proposal(raw_proposal("../../escaped"))
    try:
        with patch("ai.uav_bridge.runtime.MissionExperimentRunner", NeverRunner):
            asyncio.run(invoke_existing_runtime(ROOT, traversal, adapter=object()))
    except BridgeRuntimeInvocationDeniedError:
        pass
    else:
        raise AssertionError("unsafe scenario must be denied before runtime")
    assert NeverRunner.constructed == 0

    print(
        "SCENARIO_PATH_HARDENING=PASS contract_version=1.0 "
        "safe_segment=PASS traversal=DENY absolute=DENY nested=DENY "
        "runtime_construction=none evidence_creation=none "
        "u11_3=HARDENED u11_6=DENY_BEFORE_RUNTIME "
        "phase11=pending cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
