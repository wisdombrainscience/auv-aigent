from __future__ import annotations

import asyncio

from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.capabilities.flight.land import LandModule


class FakeLandAdapter:
    def __init__(self) -> None:
        self.state = UAVState(
            connected=True,
            armed=True,
            airborne=True,
            localization_valid=True,
        )
        self.position = UAVPosition(1.0, 2.0, -3.0)
        self.commands: list[float] = []

    async def get_state(self) -> UAVState:
        return self.state

    async def get_position(self) -> UAVPosition:
        return self.position

    async def land(self, timeout_s: float = 30.0) -> None:
        self.commands.append(timeout_s)
        self.position = UAVPosition(1.1, 2.05, 0.1)
        self.state = UAVState(
            connected=True,
            armed=False,
            airborne=False,
            localization_valid=True,
        )


VALID = {
    "task_id": "LAND-001",
    "frame": "LOCAL_NED",
    "ground_altitude_tolerance_m": 0.5,
    "horizontal_tolerance_m": 1.0,
    "timeout_s": 20.0,
}


def test_land_module_completes_and_reports_measured_state() -> None:
    adapter = FakeLandAdapter()
    result = asyncio.run(LandModule(adapter).run(dict(VALID)))

    assert result.status_value == "completed"
    assert adapter.commands == [20.0]
    assert result.data["start_altitude_m"] == 3.0
    assert result.data["final_altitude_m"] == -0.1
    assert result.data["descent_m"] == 3.1
    assert result.data["horizontal_drift_m"] < 0.2
    assert result.data["final_airborne"] is False


def test_land_module_rejects_already_landed_vehicle() -> None:
    adapter = FakeLandAdapter()
    adapter.state = UAVState(
        connected=True,
        armed=False,
        airborne=False,
        localization_valid=True,
    )

    result = asyncio.run(LandModule(adapter).run(dict(VALID)))

    assert result.status_value == "failed"
    assert result.error_code == "PRECONDITION_FAILED"
    assert "airborne=false" in (result.reason or "")


def test_land_module_rejects_nonpositive_ground_tolerance() -> None:
    adapter = FakeLandAdapter()
    data = dict(VALID)
    data["ground_altitude_tolerance_m"] = 0.0

    result = asyncio.run(LandModule(adapter).run(data))

    assert result.status_value == "failed"
    assert result.error_code == "INVALID_INPUT"
