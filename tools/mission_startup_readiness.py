#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any


async def close_adapter(adapter: Any) -> None:
    """Close an adapter and await asynchronous cleanup when supported."""
    close = getattr(adapter, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def wait_for_localization_ready(
    adapter: Any,
    *,
    timeout_s: float = 15.0,
    poll_s: float = 0.2,
) -> dict[str, Any]:
    """Wait for a fresh PX4 SITL to have valid local-position telemetry.

    This gate is intentionally read-only. It must never arm, take off, enter
    Offboard, or produce a capability verdict. TAKEOFF-001 remains responsible
    for the actual takeoff and the independent verifier remains authoritative.
    """
    timeout_s = float(timeout_s)
    poll_s = float(poll_s)
    if timeout_s <= 0:
        raise ValueError("timeout_s must be > 0")
    if poll_s <= 0:
        raise ValueError("poll_s must be > 0")

    await adapter.connect()
    deadline = time.monotonic() + timeout_s
    last_state: Any | None = None

    while True:
        state = await adapter.get_state()
        last_state = state

        if not bool(state.connected):
            raise RuntimeError("PX4 SITL connection lost during localization readiness")

        if bool(state.localization_valid):
            if bool(state.armed) or bool(state.airborne):
                raise RuntimeError(
                    "PX4 SITL readiness expected a disarmed ground vehicle "
                    f"but got armed={bool(state.armed)} airborne={bool(state.airborne)}"
                )
            position = await adapter.get_position()
            return {
                "connected": True,
                "localization_valid": True,
                "armed": False,
                "airborne": False,
                "position_m": [
                    float(position.x),
                    float(position.y),
                    float(position.z),
                ],
            }

        if time.monotonic() >= deadline:
            detail = (
                "none"
                if last_state is None
                else (
                    f"connected={bool(last_state.connected)} "
                    f"localization_valid={bool(last_state.localization_valid)} "
                    f"armed={bool(last_state.armed)} "
                    f"airborne={bool(last_state.airborne)}"
                )
            )
            raise TimeoutError(
                "PX4 SITL local position did not become valid within "
                f"{timeout_s:g}s ({detail})"
            )

        await asyncio.sleep(poll_s)
