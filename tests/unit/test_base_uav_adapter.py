import inspect

from src.adapters.base_uav_adapter import (
    UAVAdapter,
    UAVPosition,
    UAVState,
)


def test_position_model():
    p = UAVPosition(1.0, 2.0, -3.0)
    assert p.x == 1.0
    assert p.y == 2.0
    assert p.z == -3.0


def test_state_model():
    s = UAVState(
        connected=True,
        armed=False,
        airborne=False,
        localization_valid=True,
    )

    assert s.connected is True
    assert s.localization_valid is True


def test_adapter_is_abstract():
    assert inspect.isabstract(UAVAdapter)
