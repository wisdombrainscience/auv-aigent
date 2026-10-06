from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from orchestration.mission_experiment import MissionExperimentRunner
from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.resources.manager import ResourceManager
from src.telemetry.recorder import TelemetrySample
from src.verification.mission_verifier import MissionVerifier


class FakeMissionAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.airborne = False
        self.position = UAVPosition(0.0, 0.0, 0.0)
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def get_state(self) -> UAVState:
        return UAVState(
            connected=self.connected,
            armed=self.airborne,
            airborne=self.airborne,
            localization_valid=True,
        )

    async def get_position(self) -> UAVPosition:
        return self.position

    async def get_telemetry_sample(self) -> TelemetrySample:
        return TelemetrySample(
            timestamp_s=time.time(),
            x=self.position.x, y=self.position.y, z=self.position.z,
            vx=0.0, vy=0.0, vz=0.0,
            vehicle_state="AIRBORNE" if self.airborne else "DISARMED",
            localization_valid=True,
        )

    async def takeoff_to_altitude(self, target_altitude_m: float, timeout_s: float = 30.0) -> None:
        self.airborne = True
        self.position = UAVPosition(self.position.x, self.position.y, -target_altitude_m)

    async def send_position_target(self, x: float, y: float, z: float, yaw_deg: float = 0.0) -> None:
        self.position = UAVPosition(x, y, z)

    async def hold_position(self) -> None:
        return None

    async def land(self, timeout_s: float = 30.0) -> None:
        self.position = UAVPosition(self.position.x, self.position.y, 0.0)
        self.airborne = False

    async def abort(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def test_reference_mission_runs_four_verified_steps_on_one_adapter(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    mission_data = json.loads(
        (project / "config/examples/mission_reference.json").read_text(encoding="utf-8")
    )
    runner = MissionExperimentRunner(project)
    runner.runs_root = tmp_path / "runs"
    runner.capabilities.runs_root = runner.runs_root
    runner.capabilities.resources = ResourceManager(
        runner.runs_root, runner.capabilities.resource_policy
    )
    runner.verifier = MissionVerifier(project)
    runner.verifier.runs_root = runner.runs_root
    adapter = FakeMissionAdapter()

    progress: list[dict] = []
    result = asyncio.run(
        runner.run(
            mission_data=mission_data,
            scenario_id="UNIT-MISSION",
            adapter=adapter,
            step_hook=lambda record: progress.append(record),
        )
    )

    assert result.overall == "PASS"
    assert [item["capability_id"] for item in progress] == [
        "TAKEOFF-001", "NAV-001", "RETURN-HOME-001", "LAND-001"
    ]
    assert [step["capability_id"] for step in result.step_results] == [
        "TAKEOFF-001", "NAV-001", "RETURN-HOME-001", "LAND-001"
    ]
    assert result.verification["checks"]["continuity"] == "PASS"
    assert result.verification["checks"]["terminal_landed_state"] == "PASS"
    assert result.verification["checks"]["terminal_home"] == "PASS"
    assert (result.attempt_dir / "finalized.json").is_file()
    assert adapter.closed is True


def test_mission_step_event_hook_reports_running_then_terminal(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    mission_data = json.loads(
        (project / "config/examples/mission_reference.json").read_text(encoding="utf-8")
    )
    runner = MissionExperimentRunner(project)
    runner.runs_root = tmp_path / "runs"
    runner.capabilities.runs_root = runner.runs_root
    runner.capabilities.resources = ResourceManager(
        runner.runs_root, runner.capabilities.resource_policy
    )
    runner.verifier = MissionVerifier(project)
    runner.verifier.runs_root = runner.runs_root
    events: list[dict] = []

    result = asyncio.run(
        runner.run(
            mission_data=mission_data,
            scenario_id="UNIT-MISSION-EVENTS",
            adapter=FakeMissionAdapter(),
            step_event_hook=lambda record: events.append(record),
        )
    )

    assert result.overall == "PASS"
    assert len(events) == 8
    for sequence in range(1, 5):
        started = events[(sequence - 1) * 2]
        completed = events[(sequence - 1) * 2 + 1]
        assert started["sequence"] == sequence
        assert started["event"] == "started"
        assert started["state"] == "RUNNING"
        assert completed["sequence"] == sequence
        assert completed["event"] == "completed"
        assert completed["state"] == "PASS"
