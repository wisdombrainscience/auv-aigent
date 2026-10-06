from __future__ import annotations

import asyncio

from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.capabilities.navigation.return_home import ReturnHomeModule


class FakeReturnHomeAdapter:
    def __init__(self) -> None:
        self.state = UAVState(
            connected=True,
            armed=True,
            airborne=True,
            localization_valid=True,
        )
        self.position = UAVPosition(5.0, -2.0, -3.0)
        self.commands: list[tuple[float, float, float, float]] = []
        self.aborted = False

    async def get_state(self) -> UAVState:
        return self.state

    async def get_position(self) -> UAVPosition:
        return self.position

    async def send_position_target(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float = 0.0,
    ) -> None:
        self.commands.append((x, y, z, yaw_deg))
        self.position = UAVPosition(x + 0.1, y - 0.1, z + 0.05)

    async def abort(self) -> None:
        self.aborted = True


VALID = {
    "task_id": "RETURN-HOME-001",
    "frame": "LOCAL_NED",
    "home_position_m": [0.0, 0.0],
    "return_altitude_m": 3.0,
    "horizontal_tolerance_m": 0.5,
    "altitude_tolerance_m": 0.5,
    "timeout_s": 10.0,
}


def test_return_home_module_commands_declared_home_waypoint() -> None:
    adapter = FakeReturnHomeAdapter()
    result = asyncio.run(ReturnHomeModule(adapter).run(dict(VALID)))

    assert result.status_value == "completed"
    assert adapter.commands == [(0.0, 0.0, -3.0, 0.0)]
    assert result.data["home_position_m"] == [0.0, 0.0]
    assert result.data["return_altitude_m"] == 3.0
    assert result.data["horizontal_error_m"] < 0.2
    assert result.data["altitude_error_m"] < 0.1


def test_return_home_module_rejects_vehicle_already_at_home() -> None:
    adapter = FakeReturnHomeAdapter()
    adapter.position = UAVPosition(0.1, 0.1, -3.0)

    result = asyncio.run(ReturnHomeModule(adapter).run(dict(VALID)))

    assert result.status_value == "failed"
    assert result.error_code == "PRECONDITION_FAILED"
    assert "already_within_home_horizontal_tolerance" in (result.reason or "")


def test_return_home_module_rejects_nonpositive_return_altitude() -> None:
    adapter = FakeReturnHomeAdapter()
    data = dict(VALID)
    data["return_altitude_m"] = 0.0

    result = asyncio.run(ReturnHomeModule(adapter).run(data))

    assert result.status_value == "failed"
    assert result.error_code == "INVALID_INPUT"


class SettlingReturnHomeAdapter(FakeReturnHomeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.after_command = False
        self.samples = [
            UAVPosition(0.40, 0.0, -3.0),  # inside verifier 0.5, outside control 0.3
            UAVPosition(0.65, 0.0, -3.0),  # still moving
            UAVPosition(0.20, 0.0, -3.0),
            UAVPosition(0.18, 0.0, -3.0),
            UAVPosition(0.15, 0.0, -3.0),
        ]
        self.sample_index = 0

    async def get_position(self) -> UAVPosition:
        if not self.after_command:
            return self.position
        p = self.samples[min(self.sample_index, len(self.samples) - 1)]
        self.sample_index += 1
        return p

    async def send_position_target(
        self, x: float, y: float, z: float, yaw_deg: float = 0.0
    ) -> None:
        self.commands.append((x, y, z, yaw_deg))
        self.after_command = True


def test_return_home_waits_inside_inner_envelope_before_completion() -> None:
    adapter = SettlingReturnHomeAdapter()
    result = asyncio.run(
        ReturnHomeModule(adapter, poll_interval_s=0.001).run(dict(VALID))
    )

    assert result.status_value == "completed"
    assert result.data["horizontal_error_m"] <= 0.30
    assert adapter.sample_index >= 5
