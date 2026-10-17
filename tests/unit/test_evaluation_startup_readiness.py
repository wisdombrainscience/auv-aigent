import asyncio
from types import SimpleNamespace

from evaluation.direct_runtime import wait_flight_ready


class _FakeAdapter:
    def __init__(self, health_values):
        self.health_values = list(health_values)
        self.health_calls = 0
        self.closed = False

    async def connect(self):
        return None

    async def get_state(self):
        return SimpleNamespace(
            connected=True,
            localization_valid=True,
            armed=False,
            airborne=False,
        )

    async def _cached_telemetry(self, name):
        assert name == "health"
        idx = min(self.health_calls, len(self.health_values) - 1)
        self.health_calls += 1
        return self.health_values[idx]

    async def get_position(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    async def close(self):
        self.closed = True


def test_wait_flight_ready_prefers_is_armable():
    holder = {}

    def factory():
        adapter = _FakeAdapter([
            SimpleNamespace(
                is_local_position_ok=True,
                is_global_position_ok=True,
                is_home_position_ok=True,
                is_armable=False,
            ),
            SimpleNamespace(
                is_local_position_ok=True,
                is_global_position_ok=True,
                is_home_position_ok=True,
                is_armable=True,
            ),
        ])
        holder["adapter"] = adapter
        return adapter

    result = asyncio.run(
        wait_flight_ready(
            timeout_s=1.0,
            poll_s=0.001,
            stable_s=0.0,
            adapter_factory=factory,
        )
    )
    assert result["ready"] is True
    assert result["readiness_source"] == "is_armable"
    assert result["health_is_armable"] is True
    assert holder["adapter"].health_calls >= 2
    assert holder["adapter"].closed is True


def test_wait_flight_ready_falls_back_to_global_and_home_health():
    holder = {}

    def factory():
        adapter = _FakeAdapter([
            SimpleNamespace(
                is_local_position_ok=True,
                is_global_position_ok=False,
                is_home_position_ok=True,
            ),
            SimpleNamespace(
                is_local_position_ok=True,
                is_global_position_ok=True,
                is_home_position_ok=True,
            ),
        ])
        holder["adapter"] = adapter
        return adapter

    result = asyncio.run(
        wait_flight_ready(
            timeout_s=1.0,
            poll_s=0.001,
            stable_s=0.0,
            adapter_factory=factory,
        )
    )
    assert result["ready"] is True
    assert result["readiness_source"] == "global_home_fallback"
    assert result["health_global_position_ok"] is True
    assert result["health_home_position_ok"] is True
    assert holder["adapter"].closed is True
