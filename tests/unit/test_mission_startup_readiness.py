from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "mission_startup_readiness.py"
spec = importlib.util.spec_from_file_location("mission_startup_readiness", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


@dataclass
class State:
    connected: bool = True
    localization_valid: bool = False
    armed: bool = False
    airborne: bool = False


@dataclass
class Position:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class FakeAdapter:
    def __init__(self, states):
        self.states = list(states)
        self.index = 0
        self.connected_calls = 0
        self.closed = False

    async def connect(self):
        self.connected_calls += 1

    async def get_state(self):
        if self.index < len(self.states) - 1:
            state = self.states[self.index]
            self.index += 1
            return state
        return self.states[-1]

    async def get_position(self):
        return Position(0.1, -0.2, 0.03)

    async def close(self):
        self.closed = True


def test_wait_for_localization_ready_is_read_only_and_bounded():
    adapter = FakeAdapter([
        State(localization_valid=False),
        State(localization_valid=False),
        State(localization_valid=True),
    ])
    result = asyncio.run(
        mod.wait_for_localization_ready(adapter, timeout_s=0.2, poll_s=0.001)
    )
    assert result["connected"] is True
    assert result["localization_valid"] is True
    assert result["armed"] is False
    assert result["airborne"] is False
    assert result["position_m"] == [0.1, -0.2, 0.03]
    assert adapter.connected_calls == 1
    assert not hasattr(adapter, "arm")
    assert not hasattr(adapter, "takeoff")


def test_wait_for_localization_ready_times_out():
    adapter = FakeAdapter([State(localization_valid=False)])
    try:
        asyncio.run(
            mod.wait_for_localization_ready(adapter, timeout_s=0.01, poll_s=0.001)
        )
    except TimeoutError as exc:
        assert "local position did not become valid" in str(exc)
    else:
        raise AssertionError("expected TimeoutError")


def test_wait_for_localization_ready_rejects_armed_vehicle():
    adapter = FakeAdapter([State(localization_valid=True, armed=True)])
    try:
        asyncio.run(
            mod.wait_for_localization_ready(adapter, timeout_s=0.1, poll_s=0.001)
        )
    except RuntimeError as exc:
        assert "expected a disarmed ground vehicle" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_close_adapter_awaits_async_cleanup():
    adapter = FakeAdapter([State(localization_valid=True)])
    asyncio.run(mod.close_adapter(adapter))
    assert adapter.closed is True
