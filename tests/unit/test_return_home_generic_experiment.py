from __future__ import annotations

import asyncio
from pathlib import Path

from orchestration.capability_experiment import CapabilityExperimentRunner
from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.telemetry.recorder import TelemetrySample


class FakeReturnHomeRuntimeAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.position = UAVPosition(5.0, 0.0, -3.0)
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
        self.position = UAVPosition(x + 0.05, y - 0.05, z + 0.05)
        await asyncio.sleep(0)

    async def abort(self) -> None:
        self.offboard = False

    async def close(self) -> None:
        self.closed = True


def test_generic_runner_executes_return_home_and_finalizes_evidence(
    tmp_path: Path,
) -> None:
    project = Path(__file__).resolve().parents[2]
    runner = CapabilityExperimentRunner(project)
    runner.runs_root = tmp_path / "runs"
    from src.resources.manager import ResourceManager

    runner.resources = ResourceManager(runner.runs_root, runner.resource_policy)
    adapter = FakeReturnHomeRuntimeAdapter()

    result = asyncio.run(
        runner.run(
            "RETURN-HOME-001",
            input_data={
                "task_id": "RETURN-HOME-001",
                "frame": "LOCAL_NED",
                "home_position_m": [0.0, 0.0],
                "return_altitude_m": 3.0,
                "horizontal_tolerance_m": 0.5,
                "altitude_tolerance_m": 0.5,
                "timeout_s": 10.0,
            },
            scenario_id="UNIT",
            adapter=adapter,
        )
    )

    assert result.overall == "PASS"
    assert result.evidence_complete is True
    assert result.verification["checks"]["return_transition"] == "PASS"
    assert (result.attempt_dir / "finalized.json").is_file()
    assert (result.attempt_dir / "verification.json").is_file()
    assert adapter.closed is True
