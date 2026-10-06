from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

import ai.uav_bridge.runtime as runtime_module
from ai.uav_bridge import (
    BridgeRuntimeInvocationDeniedError,
    RUNTIME_INVOCATION_CONTRACT_VERSION,
    invoke_existing_runtime,
    parse_simulation_proposal,
)
from orchestration.mission_experiment import MissionExperimentResult, MissionExperimentRunner as ExistingMissionExperimentRunner
from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.resources.manager import ResourceManager
from src.telemetry.recorder import TelemetrySample
from src.verification.mission_verifier import MissionVerifier


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


def proposal(*, mission_id: str = "MISSION-001", capability_id: str = "NAV-001", scenario_id: str = "U11-6-TEST"):
    return parse_simulation_proposal(
        {
            "project_id": "UAV-PROJ-000001",
            "requirement_version": 1,
            "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
            "scenario_id": scenario_id,
            "execution_scope": "SIMULATION_ONLY",
            "mission_id": mission_id,
            "mission_payload": mission_payload(
                mission_id=mission_id,
                capability_id=capability_id,
            ),
        }
    )


class RecordingRunner:
    instances: list["RecordingRunner"] = []
    result: MissionExperimentResult | None = None
    error: Exception | None = None

    def __init__(self, project: Path) -> None:
        self.project = Path(project)
        self.calls: list[dict[str, Any]] = []
        type(self).instances.append(self)

    async def run(self, **kwargs: Any) -> MissionExperimentResult:
        self.calls.append(kwargs)
        if type(self).error is not None:
            raise type(self).error
        assert type(self).result is not None
        return type(self).result


@pytest.fixture(autouse=True)
def reset_runner():
    RecordingRunner.instances = []
    RecordingRunner.error = None
    RecordingRunner.result = MissionExperimentResult(
        run_id="MISSION001-TEST",
        mission_id="MISSION-001",
        attempt_dir=Path("/tmp/u11_6_attempt"),
        step_results=(),
        verification={
            "overall": "PASS",
            "checks": {"independent_verifier": "PASS"},
        },
        adapter_cleanup_error=None,
    )
    yield
    RecordingRunner.instances = []
    RecordingRunner.error = None
    RecordingRunner.result = None


def test_contract_version_is_frozen():
    assert RUNTIME_INVOCATION_CONTRACT_VERSION == "1.0"


def test_authorized_proposal_invokes_existing_runner_once_and_forwards_unchanged(monkeypatch):
    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", RecordingRunner)
    p = proposal()
    adapter = object()
    steps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    result = asyncio.run(
        invoke_existing_runtime(
            PROJECT,
            p,
            adapter=adapter,
            step_hook=steps.append,
            step_event_hook=events.append,
        )
    )

    assert result is RecordingRunner.result
    assert len(RecordingRunner.instances) == 1
    runner = RecordingRunner.instances[0]
    assert runner.project == PROJECT.resolve()
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["mission_data"] == p.to_dict()["mission_payload"]
    assert call["scenario_id"] == p.scenario_id
    assert call["adapter"] is adapter
    assert call["step_hook"] is steps.append or callable(call["step_hook"])
    assert call["step_event_hook"] is events.append or callable(call["step_event_hook"])


def test_mission_authorization_failure_blocks_runtime_construction(monkeypatch):
    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", RecordingRunner)
    p = proposal(mission_id="FUTURE-MISSION-777")

    with pytest.raises(BridgeRuntimeInvocationDeniedError) as exc:
        asyncio.run(invoke_existing_runtime(PROJECT, p, adapter=object()))

    assert "u11_5.mission_allowlist" in str(exc.value)
    assert RecordingRunner.instances == []


def test_capability_authorization_failure_blocks_runtime_construction(monkeypatch):
    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", RecordingRunner)
    p = proposal(capability_id="FUTURE-CAP-999")

    with pytest.raises(BridgeRuntimeInvocationDeniedError) as exc:
        asyncio.run(invoke_existing_runtime(PROJECT, p, adapter=object()))

    assert "u11_5.capability_authorization" in str(exc.value)
    assert RecordingRunner.instances == []



def test_unsafe_scenario_path_is_denied_before_runtime_construction(monkeypatch):
    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", RecordingRunner)
    p = proposal(scenario_id="../../escaped")

    with pytest.raises(BridgeRuntimeInvocationDeniedError) as exc:
        asyncio.run(invoke_existing_runtime(PROJECT, p, adapter=object()))

    assert "u11_5.capability_authorization" in str(exc.value)
    assert RecordingRunner.instances == []

def test_runtime_exception_propagates_without_fabricated_result(monkeypatch):
    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", RecordingRunner)
    RecordingRunner.error = RuntimeError("existing runtime failed")

    with pytest.raises(RuntimeError, match="existing runtime failed"):
        asyncio.run(invoke_existing_runtime(PROJECT, proposal(), adapter=object()))

    assert len(RecordingRunner.instances) == 1


def test_runtime_verdict_is_returned_unchanged(monkeypatch):
    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", RecordingRunner)
    failed = MissionExperimentResult(
        run_id="MISSION001-FAILED",
        mission_id="MISSION-001",
        attempt_dir=Path("/tmp/u11_6_failed"),
        step_results=(),
        verification={
            "overall": "FAIL",
            "checks": {"independent_verifier": "FAIL"},
        },
        adapter_cleanup_error="cleanup warning",
    )
    RecordingRunner.result = failed

    result = asyncio.run(
        invoke_existing_runtime(PROJECT, proposal(), adapter=object())
    )

    assert result is failed
    assert result.overall == "FAIL"
    assert result.verification["overall"] == "FAIL"


def test_none_adapter_is_rejected_before_authorization_runtime():
    with pytest.raises(TypeError, match="trusted runtime adapter"):
        asyncio.run(invoke_existing_runtime(PROJECT, proposal(), adapter=None))


def test_wrong_proposal_type_is_programmer_error():
    with pytest.raises(TypeError, match="SimulationProposal"):
        asyncio.run(
            invoke_existing_runtime(PROJECT, {}, adapter=object())  # type: ignore[arg-type]
        )


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
            x=self.position.x,
            y=self.position.y,
            z=self.position.z,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            vehicle_state="AIRBORNE" if self.airborne else "DISARMED",
            localization_valid=True,
        )

    async def takeoff_to_altitude(
        self,
        target_altitude_m: float,
        timeout_s: float = 30.0,
    ) -> None:
        self.airborne = True
        self.position = UAVPosition(
            self.position.x,
            self.position.y,
            -target_altitude_m,
        )

    async def send_position_target(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float = 0.0,
    ) -> None:
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


def test_bridge_reaches_real_existing_runtime_and_independent_verifier(
    monkeypatch,
    tmp_path: Path,
):
    created: list[ExistingMissionExperimentRunner] = []

    def factory(project: Path):
        runner = ExistingMissionExperimentRunner(project)
        runner.runs_root = tmp_path / "runs"
        runner.capabilities.runs_root = runner.runs_root
        runner.capabilities.resources = ResourceManager(
            runner.runs_root,
            runner.capabilities.resource_policy,
        )
        runner.verifier = MissionVerifier(project)
        runner.verifier.runs_root = runner.runs_root
        created.append(runner)
        return runner

    monkeypatch.setattr(runtime_module, "MissionExperimentRunner", factory)
    adapter = FakeMissionAdapter()

    result = asyncio.run(
        invoke_existing_runtime(PROJECT, proposal(), adapter=adapter)
    )

    assert len(created) == 1
    assert result.overall == "PASS"
    assert result.verification["overall"] == "PASS"
    assert result.verification["checks"]["child_verdicts"] == "PASS"
    assert result.verification["checks"]["child_evidence_integrity"] == "PASS"
    assert result.verification["checks"]["terminal_home"] == "PASS"
    assert len(result.step_results) == 4
    assert (result.attempt_dir / "finalized.json").is_file()
    assert adapter.closed is True
