from __future__ import annotations

import asyncio
from pathlib import Path

from orchestration.capability_experiment import CapabilityExperimentRunner
from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.resources.manager import ResourceManager
from src.telemetry.recorder import TelemetrySample


class SharedAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.airborne = False
        self.altitude = 0.0
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def get_state(self) -> UAVState:
        return UAVState(self.connected, self.airborne, self.airborne, True)

    async def get_position(self) -> UAVPosition:
        return UAVPosition(0.0, 0.0, -self.altitude)

    async def get_telemetry_sample(self) -> TelemetrySample:
        import time
        return TelemetrySample(
            timestamp_s=time.time(), x=0.0, y=0.0, z=-self.altitude,
            vx=0.0, vy=0.0, vz=0.0,
            vehicle_state="AIRBORNE" if self.airborne else "DISARMED",
            localization_valid=True,
        )

    async def takeoff_to_altitude(self, target_altitude_m: float, timeout_s: float = 30.0) -> None:
        self.altitude = target_altitude_m
        self.airborne = True

    async def close(self) -> None:
        self.closed = True


def test_generic_runner_can_leave_shared_adapter_open(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    runner = CapabilityExperimentRunner(project)
    runner.runs_root = tmp_path / "runs"
    runner.resources = ResourceManager(runner.runs_root, runner.resource_policy)
    adapter = SharedAdapter()

    result = asyncio.run(
        runner.run(
            "TAKEOFF-001",
            input_data={
                "task_id": "TAKEOFF-001",
                "frame": "LOCAL_NED",
                "target_altitude_m": 3.0,
                "altitude_tolerance_m": 0.5,
                "horizontal_tolerance_m": 1.0,
                "timeout_s": 10.0,
            },
            scenario_id="SHARED",
            adapter=adapter,
            close_adapter=False,
        )
    )

    assert result.overall == "PASS"
    assert adapter.closed is False
