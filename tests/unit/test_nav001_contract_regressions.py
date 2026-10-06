import asyncio

from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.capabilities.navigation.go_to_target import GoToTargetModule


def canonical_input(timeout_s=1.0):
    return {
        "task_id": "NAV-001",
        "scenario_id": "CONTRACT-REGRESSION",
        "frame": "LOCAL_NED",
        "target_position_m": [10.0, 5.0, -3.0],
        "position_tolerance_m": 0.5,
        "timeout_s": timeout_s,
    }


class SuccessAdapter:
    def __init__(self):
        self.aborted = False
        self.index = 0
        self.positions = [
            UAVPosition(0.0, 0.0, -2.0),
            UAVPosition(5.0, 2.5, -2.5),
            UAVPosition(9.9, 5.0, -3.0),
        ]

    async def get_state(self):
        return UAVState(
            connected=True,
            armed=True,
            airborne=True,
            localization_valid=True,
        )

    async def get_position(self):
        value = self.positions[
            min(self.index, len(self.positions) - 1)
        ]
        self.index += 1
        return value

    async def send_position_target(
        self,
        x,
        y,
        z,
        yaw_deg=0.0,
    ):
        return None

    async def abort(self):
        self.aborted = True


class LocalizationLossAdapter(SuccessAdapter):
    def __init__(self):
        super().__init__()
        self.state_calls = 0

    async def get_state(self):
        self.state_calls += 1
        return UAVState(
            connected=True,
            armed=True,
            airborne=True,
            localization_valid=(
                self.state_calls == 1
            ),
        )


class StuckAdapter(SuccessAdapter):
    async def get_position(self):
        return UAVPosition(
            0.0,
            0.0,
            -2.0,
        )


class DisconnectedAdapter(SuccessAdapter):
    async def get_state(self):
        return UAVState(
            connected=False,
            armed=False,
            airborne=False,
            localization_valid=False,
        )


def test_success_result_matches_frozen_success_contract():
    adapter = SuccessAdapter()

    result = asyncio.run(
        GoToTargetModule(
            adapter,
            poll_interval_s=0.001,
        ).run(canonical_input())
    )

    assert result.status_value == "completed"
    assert result.data["task_id"] == "NAV-001"
    assert result.data["status"] == "completed"
    assert result.data["target_position"] == [
        10.0,
        5.0,
        -3.0,
    ]
    assert result.data["final_position"] == [
        9.9,
        5.0,
        -3.0,
    ]
    assert result.data["position_error_m"] <= 0.5
    assert result.data["elapsed_time_s"] >= 0.0


def test_localization_loss_during_execute_aborts():
    adapter = LocalizationLossAdapter()

    result = asyncio.run(
        GoToTargetModule(
            adapter,
            poll_interval_s=0.001,
        ).run(canonical_input())
    )

    assert result.status_value == "failed"
    assert result.error_code == "LOCALIZATION_LOST"
    assert adapter.aborted is True


def test_timeout_aborts():
    adapter = StuckAdapter()

    result = asyncio.run(
        GoToTargetModule(
            adapter,
            poll_interval_s=0.001,
        ).run(
            canonical_input(
                timeout_s=0.02
            )
        )
    )

    assert result.status_value == "failed"
    assert result.error_code == "TIMEOUT"
    assert adapter.aborted is True


def test_disconnected_precondition_fails():
    adapter = DisconnectedAdapter()

    result = asyncio.run(
        GoToTargetModule(
            adapter,
            poll_interval_s=0.001,
        ).run(canonical_input())
    )

    assert result.status_value == "failed"
    assert result.error_code == "PRECONDITION_FAILED"


def test_nonpositive_poll_interval_is_rejected():
    try:
        GoToTargetModule(
            SuccessAdapter(),
            poll_interval_s=0,
        )
    except ValueError:
        return

    raise AssertionError(
        "expected ValueError"
    )
