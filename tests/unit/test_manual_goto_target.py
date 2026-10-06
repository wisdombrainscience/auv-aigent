import asyncio

from src.adapters.base_uav_adapter import (
    UAVPosition,
    UAVState,
)
from src.capabilities.navigation.go_to_target import (
    GoToTargetModule,
)


class FakeAdapter:
    def __init__(
        self,
        *,
        airborne=True,
        localization=True,
    ):
        self.positions = [
            UAVPosition(
                x=0.0,
                y=0.0,
                z=-2.0,
            ),
            UAVPosition(
                x=5.0,
                y=2.5,
                z=-2.5,
            ),
            UAVPosition(
                x=9.9,
                y=5.0,
                z=-3.0,
            ),
        ]

        self.index = 0
        self.airborne = airborne
        self.localization = localization
        self.sent = None
        self.aborted = False

    async def connect(self):
        return None

    async def get_state(self):
        return UAVState(
            connected=True,
            armed=True,
            airborne=self.airborne,
            localization_valid=(
                self.localization
            ),
        )

    async def get_position(self):
        p = self.positions[
            min(
                self.index,
                len(self.positions) - 1,
            )
        ]

        self.index += 1
        return p

    async def send_position_target(
        self,
        x,
        y,
        z,
        yaw_deg=0.0,
    ):
        self.sent = (
            x,
            y,
            z,
            yaw_deg,
        )

    async def abort(self):
        self.aborted = True


def canonical_input():
    return {
        "task_id": "NAV-001",
        "scenario_id": "MANUAL",
        "frame": "LOCAL_NED",
        "target_position_m": [
            10.0,
            5.0,
            -3.0,
        ],
        "position_tolerance_m": 0.5,
        "timeout_s": 5.0,
    }


def test_manual_baseline_reaches_target():
    adapter = FakeAdapter()

    module = GoToTargetModule(
        adapter,
        poll_interval_s=0.001,
    )

    result = asyncio.run(
        module.run(
            canonical_input()
        )
    )

    assert (
        result.status_value
        == "completed"
    )

    assert (
        result.data[
            "position_error_m"
        ]
        <= 0.5
    )

    assert adapter.sent == (
        10.0,
        5.0,
        -3.0,
        0.0,
    )

    # On successful target acquisition, keep the final
    # Offboard position target active until the experiment
    # orchestrator has captured terminal telemetry.
    #
    # Cleanup/abort is owned by the outer orchestrator.
    assert adapter.aborted is False


def test_not_airborne_fails():
    adapter = FakeAdapter(
        airborne=False
    )

    module = GoToTargetModule(
        adapter
    )

    result = asyncio.run(
        module.run(
            canonical_input()
        )
    )

    assert (
        result.status_value
        == "failed"
    )

    assert (
        result.error_code
        == "PRECONDITION_FAILED"
    )

    assert adapter.sent is None


def test_localization_precondition_fails():
    adapter = FakeAdapter(
        localization=False
    )

    module = GoToTargetModule(
        adapter
    )

    result = asyncio.run(
        module.run(
            canonical_input()
        )
    )

    assert (
        result.status_value
        == "failed"
    )

    assert (
        result.error_code
        == "PRECONDITION_FAILED"
    )


def test_unsupported_frame_fails():
    adapter = FakeAdapter()

    input_data = canonical_input()
    input_data["frame"] = "GLOBAL"

    module = GoToTargetModule(
        adapter
    )

    result = asyncio.run(
        module.run(input_data)
    )

    assert (
        result.status_value
        == "failed"
    )

    assert (
        result.error_code
        == "UNSUPPORTED_FRAME"
    )


def test_manual_baseline_requires_settled_inner_arrival_before_completion():
    adapter = FakeAdapter()
    # The first near-target sample is inside the frozen 0.5 m verifier radius
    # but outside the controller's tighter completion envelope.  A robust
    # controller must keep commanding until it has actually settled closer.
    adapter.positions = [
        UAVPosition(0.0, 0.0, -2.0),
        UAVPosition(9.56, 5.0, -3.0),   # 0.44 m: verifier would pass, do not stop.
        UAVPosition(9.35, 5.0, -3.0),   # 0.65 m: overshoot / still moving.
        UAVPosition(9.80, 5.0, -3.0),   # 0.20 m.
        UAVPosition(9.82, 5.0, -3.0),   # 0.18 m.
        UAVPosition(9.85, 5.0, -3.0),   # 0.15 m, settled.
    ]

    result = asyncio.run(
        GoToTargetModule(adapter, poll_interval_s=0.001).run(canonical_input())
    )

    assert result.status_value == "completed"
    assert result.data["position_error_m"] <= 0.30
    assert adapter.index >= 6
