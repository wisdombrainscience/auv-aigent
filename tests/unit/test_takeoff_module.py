from __future__ import annotations

import asyncio

from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.capabilities.flight.takeoff import TakeoffModule


class FakeTakeoffAdapter:
    def __init__(self) -> None:
        self.state = UAVState(
            connected=True,
            armed=False,
            airborne=False,
            localization_valid=True,
        )
        self.position = UAVPosition(1.0, 2.0, 0.0)
        self.commands: list[tuple[float, float]] = []

    async def get_state(self) -> UAVState:
        return self.state

    async def get_position(self) -> UAVPosition:
        return self.position

    async def takeoff_to_altitude(
        self,
        target_altitude_m: float,
        timeout_s: float = 30.0,
    ) -> None:
        self.commands.append((target_altitude_m, timeout_s))
        self.position = UAVPosition(1.05, 2.02, -target_altitude_m)
        self.state = UAVState(
            connected=True,
            armed=True,
            airborne=True,
            localization_valid=True,
        )


VALID = {
    "task_id": "TAKEOFF-001",
    "frame": "LOCAL_NED",
    "target_altitude_m": 3.0,
    "altitude_tolerance_m": 0.5,
    "horizontal_tolerance_m": 1.0,
    "timeout_s": 20.0,
}


def test_takeoff_module_completes_and_reports_measured_state() -> None:
    adapter = FakeTakeoffAdapter()
    result = asyncio.run(TakeoffModule(adapter).run(dict(VALID)))

    assert result.status_value == "completed"
    assert adapter.commands == [(3.0, 20.0)]
    assert result.data["target_altitude_m"] == 3.0
    assert result.data["measured_altitude_m"] == 3.0
    assert result.data["altitude_error_m"] == 0.0
    assert result.data["horizontal_drift_m"] < 0.1


def test_takeoff_module_rejects_already_airborne_vehicle() -> None:
    adapter = FakeTakeoffAdapter()
    adapter.state = UAVState(
        connected=True,
        armed=True,
        airborne=True,
        localization_valid=True,
    )

    result = asyncio.run(TakeoffModule(adapter).run(dict(VALID)))

    assert result.status_value == "failed"
    assert result.error_code == "PRECONDITION_FAILED"
    assert "airborne=true" in (result.reason or "")


def test_takeoff_module_rejects_nonpositive_target_altitude() -> None:
    adapter = FakeTakeoffAdapter()
    data = dict(VALID)
    data["target_altitude_m"] = 0.0

    result = asyncio.run(TakeoffModule(adapter).run(data))

    assert result.status_value == "failed"
    assert result.error_code == "INVALID_INPUT"
