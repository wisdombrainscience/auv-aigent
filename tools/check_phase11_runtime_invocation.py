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

from ai.uav_bridge import (
    BridgeRuntimeInvocationDeniedError,
    RUNTIME_INVOCATION_CONTRACT_VERSION,
    invoke_existing_runtime,
    parse_simulation_proposal,
)
from orchestration.mission_experiment import MissionExperimentResult


def raw_proposal(
    *,
    mission_id: str = "MISSION-001",
    capability_id: str = "NAV-001",
):
    return {
        "project_id": "UAV-PROJ-000001",
        "requirement_version": 1,
        "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
        "scenario_id": "U11-6-ACCEPTANCE",
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


class ProbeRunner:
    constructed = 0
    calls: list[dict[str, Any]] = []

    def __init__(self, project: Path) -> None:
        type(self).constructed += 1
        self.project = Path(project).resolve()

    async def run(self, **kwargs: Any) -> MissionExperimentResult:
        type(self).calls.append(kwargs)
        return MissionExperimentResult(
            run_id="MISSION001-U11-6-ACCEPTANCE",
            mission_id="MISSION-001",
            attempt_dir=ROOT / "runs" / "probe-not-created",
            step_results=(),
            verification={
                "overall": "PASS",
                "checks": {"probe_independent_verifier": "PASS"},
            },
            adapter_cleanup_error=None,
        )


def main() -> int:
    assert RUNTIME_INVOCATION_CONTRACT_VERSION == "1.0"
    p = parse_simulation_proposal(raw_proposal())
    adapter = object()

    with patch("ai.uav_bridge.runtime.MissionExperimentRunner", ProbeRunner):
        result = asyncio.run(
            invoke_existing_runtime(ROOT, p, adapter=adapter)
        )

    assert result.run_id == "MISSION001-U11-6-ACCEPTANCE"
    assert result.verification["overall"] == "PASS"
    assert ProbeRunner.constructed == 1
    assert len(ProbeRunner.calls) == 1
    call = ProbeRunner.calls[0]
    assert call["mission_data"] == p.to_dict()["mission_payload"]
    assert call["scenario_id"] == p.scenario_id
    assert call["adapter"] is adapter

    denied_before = ProbeRunner.constructed
    denied = parse_simulation_proposal(
        raw_proposal(mission_id="FUTURE-MISSION-777")
    )
    try:
        with patch("ai.uav_bridge.runtime.MissionExperimentRunner", ProbeRunner):
            asyncio.run(
                invoke_existing_runtime(ROOT, denied, adapter=object())
            )
    except BridgeRuntimeInvocationDeniedError:
        pass
    else:
        raise AssertionError("unauthorized mission must not reach runtime")
    assert ProbeRunner.constructed == denied_before

    print(
        "RUNTIME_INVOCATION=PASS contract_version=1.0 "
        "prerequisite=U11_5_AUTHORIZED "
        "runtime=EXISTING_MissionExperimentRunner "
        "payload=UNCHANGED scenario=FROM_PROPOSAL "
        "unauthorized=DENY_BEFORE_RUNTIME "
        "runtime_result=UNCHANGED "
        "verdict_authority=independent_mission_verifier "
        "bridge_persistence=none px4_instantiation=none raw_mavsdk=none "
        "uav_run_link=reserved_for_U11_7 phase11=pending cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
