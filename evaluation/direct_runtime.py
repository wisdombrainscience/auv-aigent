from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, Callable

from evaluation.common import PROJECT
from orchestration.capability_experiment import CapabilityExperimentRunner
from orchestration.step37a_simulation import SimulationSupervisor
from src.adapters.px4_sitl_adapter import PX4SITLAdapter


TAKEOFF_INPUT = {
    "task_id": "TAKEOFF-001",
    "frame": "LOCAL_NED",
    "target_altitude_m": 3.0,
    "altitude_tolerance_m": 0.5,
    "horizontal_tolerance_m": 1.0,
    "timeout_s": 30.0,
}

RTH_INPUT = {
    "task_id": "RETURN-HOME-001",
    "frame": "LOCAL_NED",
    "home_position_m": [0.0, 0.0],
    "return_altitude_m": 3.0,
    "horizontal_tolerance_m": 0.5,
    "altitude_tolerance_m": 0.5,
    "timeout_s": 30.0,
}

LAND_INPUT = {
    "task_id": "LAND-001",
    "frame": "LOCAL_NED",
    "ground_altitude_tolerance_m": 0.5,
    "horizontal_tolerance_m": 1.0,
    "timeout_s": 30.0,
}


def nav_input(target: list[float], timeout_s: float = 30.0) -> dict[str, Any]:
    return {
        "task_id": "NAV-001",
        "frame": "LOCAL_NED",
        "target_position_m": [float(v) for v in target],
        "position_tolerance_m": 0.5,
        "timeout_s": float(timeout_s),
    }


async def wait_flight_ready(
    timeout_s: float = 60.0,
    poll_s: float = 0.25,
    stable_s: float = 2.0,
    adapter_factory: Callable[[], PX4SITLAdapter] = PX4SITLAdapter,
) -> dict[str, Any]:
    """Wait for PX4 to be connected, localized, and actually armable.

    Fresh SITL can report local position before EKF/global/home health has settled.
    Starting a TAKEOFF in that interval can produce MAVSDK COMMAND_DENIED even
    though the simulator is nominally RUNNING.  The evaluation clock/sample is
    allowed to start only after the readiness condition remains true for
    ``stable_s`` seconds.
    """
    if timeout_s <= 0:
        raise ValueError("timeout_s must be > 0")
    if poll_s <= 0:
        raise ValueError("poll_s must be > 0")
    if stable_s < 0:
        raise ValueError("stable_s must be >= 0")

    adapter = adapter_factory()
    last: dict[str, Any] = {}
    stable_since: float | None = None
    try:
        await adapter.connect()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout_s)
        while loop.time() < deadline:
            try:
                state = await adapter.get_state()
                # Health is already consumed by the adapter's persistent pump.
                health = await adapter._cached_telemetry("health")  # evaluation-only readiness probe
                armable_raw = getattr(health, "is_armable", None)
                global_ok = bool(getattr(health, "is_global_position_ok", False))
                home_ok = bool(getattr(health, "is_home_position_ok", False))
                local_ok = bool(getattr(health, "is_local_position_ok", state.localization_valid))

                if armable_raw is None:
                    readiness_source = "global_home_fallback"
                    health_ready = global_ok and home_ok
                    armable_value: bool | None = None
                else:
                    readiness_source = "is_armable"
                    armable_value = bool(armable_raw)
                    health_ready = armable_value

                last = {
                    "connected": bool(state.connected),
                    "localization_valid": bool(state.localization_valid),
                    "health_local_position_ok": local_ok,
                    "health_global_position_ok": global_ok,
                    "health_home_position_ok": home_ok,
                    "health_is_armable": armable_value,
                    "readiness_source": readiness_source,
                    "ready": False,
                }

                ready_now = bool(state.connected and state.localization_valid and local_ok and health_ready)
                if ready_now:
                    pos = await adapter.get_position()
                    last["position_m"] = [pos.x, pos.y, pos.z]
                    now = loop.time()
                    if stable_since is None:
                        stable_since = now
                    held = now - stable_since
                    last["stable_s"] = round(held, 3)
                    if held >= float(stable_s):
                        last["ready"] = True
                        return last
                else:
                    stable_since = None

            except (TimeoutError, RuntimeError) as exc:
                stable_since = None
                last = {"transient_error": f"{type(exc).__name__}: {exc}", "ready": False}

            await asyncio.sleep(float(poll_s))

        raise TimeoutError(f"flight readiness not reached before {timeout_s:g}s: {last}")
    finally:
        await adapter.close()


async def wait_localization_ready(
    timeout_s: float = 60.0,
    poll_s: float = 0.25,
    stable_s: float = 2.0,
    adapter_factory: Callable[[], PX4SITLAdapter] = PX4SITLAdapter,
) -> dict[str, Any]:
    """Compatibility alias retained for comparative/evaluation callers."""
    return await wait_flight_ready(
        timeout_s=timeout_s,
        poll_s=poll_s,
        stable_s=stable_s,
        adapter_factory=adapter_factory,
    )


class TelemetryDropoutAdapter:
    """Evaluation-only proxy.

    Flight-control methods are delegated unchanged to the real PX4SITLAdapter.
    Only evidence-facing get_telemetry_sample() is altered, so this probes whether
    the independent verifier fails closed when measured telemetry loses validity.
    """

    def __init__(self, inner: PX4SITLAdapter, *, start_after: int = 4, length: int = 12) -> None:
        self.inner = inner
        self.start_after = int(start_after)
        self.length = int(length)
        self.sample_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def connect(self) -> None:
        await self.inner.connect()

    async def close(self) -> None:
        await self.inner.close()

    async def get_telemetry_sample(self) -> Any:
        sample = await self.inner.get_telemetry_sample()
        self.sample_count += 1
        lo = self.start_after + 1
        hi = self.start_after + self.length
        if lo <= self.sample_count <= hi:
            return replace(sample, localization_valid=False)
        return sample


async def run_fresh_nav_trial(
    *,
    scenario: str,
    target: list[float],
    nav_timeout_s: float = 30.0,
    telemetry_dropout: bool = False,
    startup_retries: int = 3,
    startup_wait_s: float = 60.0,
    startup_stable_s: float = 2.0,
    startup_retry_cooldown_s: float = 3.0,
) -> dict[str, Any]:
    """Run one fresh TAKEOFF->NAV trial with bounded infrastructure retries.

    Startup/readiness failures are not capability samples.  Retry the fresh SITL
    startup before opening the TAKEOFF/NAV evidence windows and always stop the
    simulator on every failed readiness attempt.  If all startup attempts fail,
    raise and let the reliability campaign mark an infrastructure abort rather
    than silently shrinking/contaminating the requested sample size.
    """
    if startup_retries <= 0:
        raise ValueError("startup_retries must be > 0")

    simulator = SimulationSupervisor(PROJECT)
    ready: dict[str, Any] | None = None
    startup_error: Exception | None = None
    startup_attempt = 0

    for startup_attempt in range(1, int(startup_retries) + 1):
        try:
            simulator.start_new([0.0, 0.0, 0.0])
            ready = await wait_flight_ready(
                timeout_s=float(startup_wait_s),
                stable_s=float(startup_stable_s),
            )
            break
        except Exception as exc:
            startup_error = exc
            try:
                simulator.stop()
            except Exception:
                pass
            if startup_attempt < int(startup_retries) and startup_retry_cooldown_s > 0:
                await asyncio.sleep(float(startup_retry_cooldown_s))

    if ready is None:
        assert startup_error is not None
        raise RuntimeError(
            f"fresh simulator flight readiness failed after {startup_retries} attempts: "
            f"{type(startup_error).__name__}: {startup_error}"
        ) from startup_error

    runner = CapabilityExperimentRunner(PROJECT)
    base = PX4SITLAdapter()
    takeoff = None
    nav = None
    cleanup_error = None
    try:
        takeoff = await runner.run(
            "TAKEOFF-001",
            input_data=dict(TAKEOFF_INPUT),
            scenario_id=f"{scenario}__TAKEOFF",
            adapter=base,
            post_action="hold",
            close_adapter=False,
        )
        nav_adapter: Any = TelemetryDropoutAdapter(base) if telemetry_dropout else base
        nav = await runner.run(
            "NAV-001",
            input_data=nav_input(target, nav_timeout_s),
            scenario_id=f"{scenario}__NAV",
            adapter=nav_adapter,
            post_action="hold",
            close_adapter=False,
        )
    finally:
        try:
            await base.close()
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"
        try:
            simulator.stop()
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = f"{type(exc).__name__}: {exc}"

    return {
        "takeoff": takeoff,
        "nav": nav,
        "cleanup_error": cleanup_error,
        "startup_attempts": startup_attempt,
        "startup_ready": ready,
    }
