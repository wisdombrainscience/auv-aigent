from __future__ import annotations

import asyncio
import json
from pathlib import Path

from orchestration.capability_experiment import CapabilityExperimentRunner
from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.resources.manager import ResourceManager
from src.telemetry.recorder import TelemetrySample


class FakeNavRuntimeAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.position = UAVPosition(0.0, 0.0, -3.0)
        self.closed = False
        self.offboard = False

    async def connect(self) -> None:
        self.connected = True

    async def get_state(self) -> UAVState:
        return UAVState(
            connected=self.connected,
            armed=True,
            airborne=True,
            localization_valid=True,
        )

    async def get_position(self) -> UAVPosition:
        return self.position

    async def get_telemetry_sample(self) -> TelemetrySample:
        import time

        return TelemetrySample(
            timestamp_s=time.time(),
            x=self.position.x,
            y=self.position.y,
            z=self.position.z,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            vehicle_state="AIRBORNE",
            localization_valid=True,
        )

    async def send_position_target(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float = 0.0,
    ) -> None:
        self.offboard = True
        self.position = UAVPosition(x + 0.05, y, z)
        await asyncio.sleep(0)

    async def abort(self) -> None:
        self.offboard = False

    async def close(self) -> None:
        self.closed = True


def test_generic_nav_runner_preserves_frozen_legacy_evidence_contract(
    tmp_path: Path,
) -> None:
    project = Path(__file__).resolve().parents[2]
    runner = CapabilityExperimentRunner(project)
    runner.runs_root = tmp_path / "runs"
    runner.resources = ResourceManager(runner.runs_root, runner.resource_policy)
    adapter = FakeNavRuntimeAdapter()

    result = asyncio.run(
        runner.run(
            "NAV-001",
            input_data={
                "task_id": "NAV-001",
                "frame": "LOCAL_NED",
                "target_position_m": [5.0, 0.0, -3.0],
                "position_tolerance_m": 0.5,
                "timeout_s": 10.0,
            },
            scenario_id="UNIT-NAV-GENERIC",
            adapter=adapter,
        )
    )

    assert result.overall == "PASS"
    assert result.evidence_complete is True
    assert adapter.closed is True

    for name in (
        "input_validation.json",
        "boundary_result.json",
        "unit_test_result.json",
        "verification.json",
        "finalized.json",
    ):
        assert (result.attempt_dir / name).is_file(), name

    input_validation = json.loads(
        (result.attempt_dir / "input_validation.json").read_text()
    )
    boundary = json.loads(
        (result.attempt_dir / "boundary_result.json").read_text()
    )
    unit_gate = json.loads(
        (result.attempt_dir / "unit_test_result.json").read_text()
    )

    assert input_validation["valid"] is True
    assert input_validation["source"] == "generic_capability_lifecycle"
    assert boundary["overall"] == "PASS"
    assert boundary["start_position_m"] == [0.0, 0.0, -3.0]
    assert boundary["target_position_m"] == [5.0, 0.0, -3.0]
    assert unit_gate["status"] == "NOT_RUN_IN_RUNTIME_EXPERIMENT"
