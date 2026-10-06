from __future__ import annotations

import asyncio
import math
import time
from typing import Any

try:
    from mavsdk import System
    from mavsdk.offboard import PositionNedYaw
except ImportError:  # allows unit/static tooling without the optional runtime wheel
    System = None  # type: ignore[assignment]
    PositionNedYaw = None  # type: ignore[assignment]

from src.adapters.base_uav_adapter import (
    UAVAdapter,
    UAVPosition,
    UAVState,
)
from src.telemetry.recorder import (
    TelemetrySample,
)


class PX4SITLAdapter(UAVAdapter):
    """
    MAVSDK adapter for PX4 SITL.

    Coordinate convention:
        LOCAL_NED

        x = north
        y = east
        z = down

    This adapter is intended for the simulation
    environment used by the NAV-001 PoC.
    """

    def __init__(
        self,
        system_address: str = "udpin://0.0.0.0:14540",
        connect_timeout_s: float = 15.0,
        telemetry_timeout_s: float = 5.0,
    ) -> None:
        if connect_timeout_s <= 0:
            raise ValueError(
                "connect_timeout_s must be > 0"
            )

        if telemetry_timeout_s <= 0:
            raise ValueError(
                "telemetry_timeout_s must be > 0"
            )

        if System is None or PositionNedYaw is None:
            raise RuntimeError(
                "mavsdk is required for PX4SITLAdapter runtime; install project dependencies"
            )

        self.system_address = system_address
        self.connect_timeout_s = (
            float(connect_timeout_s)
        )
        self.telemetry_timeout_s = (
            float(telemetry_timeout_s)
        )

        self._drone = System()

        self._connected = False
        self._offboard_started = False

        # Cached vehicle state.
        #
        # get_state() refreshes this value.
        # get_telemetry_sample() reuses it instead
        # of waiting for several slow MAVSDK streams
        # for every telemetry CSV sample.
        self._last_state: UAVState | None = None
        self._closed = False

        # Long-lived MAVSDK telemetry subscriptions.  Repeatedly creating a
        # stream, reading one item, and closing it at 5-10 Hz causes gRPC/MAVSDK
        # callback queues to accumulate even when ``aclose()`` is called.  Keep
        # one continuously-drained subscription per topic and expose cached
        # snapshots to capability code and evidence recorders instead.
        self._telemetry_tasks: dict[str, asyncio.Task[None]] = {}
        self._telemetry_values: dict[str, Any] = {}
        self._telemetry_events: dict[str, asyncio.Event] = {}
        self._telemetry_errors: dict[str, Exception] = {}

    async def close(self) -> None:
        """Release telemetry subscriptions and MAVSDK resources deterministically.

        MAVSDK-Python starts an embedded ``mavsdk_server`` subprocess when
        ``System()`` is used without an external server address. Relying on
        ``System.__del__`` delays subprocess/pipe/log-thread cleanup until GC,
        which is unsafe for long interactive sessions that create one adapter
        per capability run.  Telemetry pumps are cancelled first so their gRPC
        streams are closed before the embedded server is stopped.
        """
        if self._closed:
            return

        self._closed = True
        await self._stop_telemetry_pumps()

        self._offboard_started = False
        self._connected = False
        self._last_state = None

        stop_server = getattr(self._drone, "_stop_mavsdk_server", None)
        if callable(stop_server):
            stop_server()

        # Give MAVSDK's logging pipe thread a chance to observe EOF after the
        # embedded server is killed before the experiment is reported terminal.
        await asyncio.sleep(0.05)


    def _ensure_telemetry_runtime(self) -> None:
        """Initialize lazy asyncio telemetry state on the active event loop."""
        if not hasattr(self, "_telemetry_tasks"):
            self._telemetry_tasks = {}
        if not hasattr(self, "_telemetry_values"):
            self._telemetry_values = {}
        if not hasattr(self, "_telemetry_errors"):
            self._telemetry_errors = {}
        if not hasattr(self, "_telemetry_events"):
            self._telemetry_events = {}

        for name in ("connection", "armed", "airborne", "health", "position"):
            self._telemetry_events.setdefault(name, asyncio.Event())

    async def _consume_stream(self, name: str, stream: Any) -> None:
        """Continuously drain one MAVSDK stream into the latest-value cache."""
        self._ensure_telemetry_runtime()
        iterator = stream.__aiter__()
        try:
            while True:
                try:
                    value = await iterator.__anext__()
                except StopAsyncIteration:
                    self._telemetry_errors[name] = RuntimeError(
                        f"MAVSDK telemetry stream ended: {name}"
                    )
                    self._telemetry_events[name].set()
                    return

                self._telemetry_values[name] = value
                self._telemetry_events[name].set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._telemetry_errors[name] = exc
            self._telemetry_events[name].set()
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    def _start_telemetry_pumps(self) -> None:
        """Start one long-lived consumer per MAVSDK telemetry topic."""
        self._ensure_telemetry_runtime()
        sources = {
            "connection": self._drone.core.connection_state,
            "armed": self._drone.telemetry.armed,
            "airborne": self._drone.telemetry.in_air,
            "health": self._drone.telemetry.health,
            "position": self._drone.telemetry.position_velocity_ned,
        }

        for name, factory in sources.items():
            task = self._telemetry_tasks.get(name)
            if task is not None and not task.done():
                continue
            self._telemetry_errors.pop(name, None)
            self._telemetry_events[name].clear()
            self._telemetry_tasks[name] = asyncio.create_task(
                self._consume_stream(name, factory()),
                name=f"px4-telemetry-{name}",
            )

    async def _stop_telemetry_pumps(self) -> None:
        tasks = list(getattr(self, "_telemetry_tasks", {}).values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if hasattr(self, "_telemetry_tasks"):
            self._telemetry_tasks.clear()

    async def _cached_telemetry(self, name: str) -> Any:
        """Return the latest value after the persistent stream becomes ready."""
        self._ensure_telemetry_runtime()
        event = self._telemetry_events[name]
        if not event.is_set():
            try:
                await asyncio.wait_for(event.wait(), timeout=self.telemetry_timeout_s)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Timed out waiting for MAVSDK telemetry: {name}"
                ) from exc

        error = self._telemetry_errors.get(name)
        if error is not None:
            raise RuntimeError(
                f"MAVSDK telemetry stream failed: {name}: {error}"
            ) from error

        if name not in self._telemetry_values:
            raise RuntimeError(f"MAVSDK telemetry unavailable: {name}")
        return self._telemetry_values[name]

    async def _first(
        self,
        stream: Any,
        *,
        timeout_s: float | None = None,
    ) -> Any:
        """Return one value from a MAVSDK async stream and close it promptly.

        MAVSDK creates a user callback subscription for telemetry streams.
        Repeatedly taking only the first value without explicitly closing the
        async generator can leave callbacks queued until garbage collection.
        Closing here keeps repeated polling bounded during long sessions.
        """

        iterator = stream.__aiter__()
        timeout = (
            self.telemetry_timeout_s
            if timeout_s is None
            else timeout_s
        )

        try:
            try:
                return await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=timeout,
                )
            except StopAsyncIteration as exc:
                raise RuntimeError(
                    "MAVSDK telemetry stream ended without producing a value"
                ) from exc
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    # Stream cleanup is best effort; telemetry/read errors are
                    # still surfaced by the main read path above.
                    pass

    async def connect(self) -> None:
        """
        Connect to PX4 SITL and wait until MAVSDK
        reports an actual system connection.
        """

        if self._closed:
            raise RuntimeError("PX4SITLAdapter is closed")
        if self._connected:
            return

        await self._drone.connect(
            system_address=self.system_address
        )

        async def _wait_until_connected() -> Any:
            iterator = self._drone.core.connection_state().__aiter__()
            try:
                while True:
                    try:
                        state = await iterator.__anext__()
                    except StopAsyncIteration as exc:
                        raise RuntimeError(
                            "PX4 connection-state stream ended"
                        ) from exc
                    if state.is_connected:
                        return state
            finally:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:
                        pass

        try:
            connected_state = await asyncio.wait_for(
                _wait_until_connected(),
                timeout=self.connect_timeout_s,
            )

        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "Timed out while connecting to "
                f"PX4 SITL at {self.system_address}"
            ) from exc

        self._connected = True
        self._start_telemetry_pumps()
        # Seed connection state immediately from the readiness stream; the
        # persistent connection pump will keep it current from here on.
        self._telemetry_values["connection"] = connected_state
        self._telemetry_events["connection"].set()

    async def get_state(self) -> UAVState:
        """
        Read the current vehicle state.

        Values come from continuously-drained MAVSDK telemetry pumps.  Reading
        state therefore does not create or tear down subscriptions.

        This method also refreshes _last_state for compatibility with existing
        orchestration code.
        """

        if not self._connected:
            raise RuntimeError(
                "PX4SITLAdapter is not connected"
            )

        connection, armed, airborne, health = await asyncio.gather(
            self._cached_telemetry("connection"),
            self._cached_telemetry("armed"),
            self._cached_telemetry("airborne"),
            self._cached_telemetry("health"),
        )

        state = UAVState(
            connected=bool(
                connection.is_connected
            ),
            armed=bool(armed),
            airborne=bool(airborne),
            localization_valid=bool(
                health.is_local_position_ok
            ),
        )

        self._last_state = state

        return state

    async def prepare_airborne(
        self,
        target_down_m: float,
        timeout_s: float = 30.0,
    ) -> None:
        """
        Prepare PX4 SITL for an airborne experiment.

        SIMULATION ONLY.

        Telemetry remains authoritative. This method only requests
        the simulated vehicle to arm/take off and waits until the
        measured LOCAL_NED state satisfies the requested altitude.
        """

        if not self._connected:
            raise RuntimeError(
                "PX4SITLAdapter is not connected"
            )

        if timeout_s <= 0:
            raise ValueError(
                "timeout_s must be > 0"
            )

        if target_down_m >= 0:
            raise ValueError(
                "target_down_m must be negative for LOCAL_NED"
            )

        await self._drone.action.set_takeoff_altitude(
            abs(float(target_down_m))
        )

        localization_deadline = (
            time.monotonic() + min(float(timeout_s), 15.0)
        )

        while True:
            state = await self.get_state()

            if state.localization_valid:
                break

            if time.monotonic() >= localization_deadline:
                raise RuntimeError(
                    "PX4 SITL local position is not valid "
                    "within setup timeout"
                )

            await asyncio.sleep(0.2)

        # PX4 may report valid local position slightly before all arming
        # preconditions settle after a fresh SITL restart.  A transient
        # COMMAND_DENIED during this short boot/readiness window must not turn
        # the first capability run into a false runtime failure.  Retry only
        # the specific MAVSDK ActionError/Command Denied case, and keep the
        # retry window bounded.  Other action errors are surfaced immediately.
        arm_deadline = time.monotonic() + min(float(timeout_s), 10.0)
        last_arm_error: Exception | None = None

        while True:
            state = await self.get_state()

            if state.armed:
                break

            try:
                await self._drone.action.arm()
            except Exception as exc:
                text = str(exc).lower()
                transient_denial = (
                    type(exc).__name__ == "ActionError"
                    and (
                        "command_denied" in text
                        or "command denied" in text
                    )
                )
                if not transient_denial:
                    raise
                last_arm_error = exc

            # Whether arm() was accepted or transiently denied, allow PX4 a
            # short settling interval and re-read measured state before
            # deciding what to do next.
            await asyncio.sleep(0.5)
            state = await self.get_state()
            if state.armed:
                break

            if time.monotonic() >= arm_deadline:
                detail = (
                    f": {last_arm_error}"
                    if last_arm_error is not None
                    else ""
                )
                raise TimeoutError(
                    "PX4 SITL arming readiness timeout" + detail
                )

        state = await self.get_state()

        if not state.airborne:
            await self._drone.action.takeoff()

        deadline = time.monotonic() + float(timeout_s)

        while time.monotonic() < deadline:
            state = await self.get_state()
            position = await self.get_position()

            if not state.connected:
                raise RuntimeError(
                    "PX4 SITL connection lost during airborne setup"
                )

            if not state.localization_valid:
                await asyncio.sleep(0.2)
                continue

            if (
                state.armed
                and state.airborne
                and position.z <= target_down_m + 0.25
            ):
                return

            await asyncio.sleep(0.2)

        raise TimeoutError(
            "PX4 SITL airborne setup timeout: "
            f"target_down_m={target_down_m}"
        )

    async def takeoff_to_altitude(
        self,
        target_altitude_m: float,
        timeout_s: float = 30.0,
    ) -> None:
        """Arm and take off to a positive-up LOCAL_NED altitude.

        ``prepare_airborne`` remains the low-level simulation readiness helper
        used by legacy NAV-001 orchestration.  TAKEOFF-001 calls this public
        adapter capability so its module does not depend on PX4-specific
        private implementation details.
        """
        altitude = float(target_altitude_m)
        if not math.isfinite(altitude) or altitude <= 0:
            raise ValueError("target_altitude_m must be a finite value > 0")
        await self.prepare_airborne(
            target_down_m=-altitude,
            timeout_s=timeout_s,
        )

    async def get_position(
        self,
    ) -> UAVPosition:
        """
        Read current LOCAL_NED position.
        """

        if not self._connected:
            raise RuntimeError(
                "PX4SITLAdapter is not connected"
            )

        pv = await self._cached_telemetry("position")

        p = pv.position

        return UAVPosition(
            x=float(p.north_m),
            y=float(p.east_m),
            z=float(p.down_m),
        )

    async def get_telemetry_sample(
        self,
    ) -> TelemetrySample:
        """
        Produce one evidence sample.

        All values are snapshots from continuously-drained MAVSDK streams.
        Evidence sampling never creates a new subscription, which keeps callback
        queues bounded during long-running capabilities.
        """

        if not self._connected:
            raise RuntimeError(
                "PX4SITLAdapter is not connected"
            )

        pv = await self._cached_telemetry("position")

        # State reads are cache-only once telemetry pumps are active, so every
        # evidence row can use the latest measured arm/air/localization state
        # without creating additional MAVSDK subscriptions.
        state = await self.get_state()

        p = pv.position
        v = pv.velocity

        if state.airborne:
            vehicle_state = "AIRBORNE"

        elif state.armed:
            vehicle_state = "ARMED"

        else:
            vehicle_state = "DISARMED"

        return TelemetrySample(
            timestamp_s=time.time(),

            x=float(p.north_m),
            y=float(p.east_m),
            z=float(p.down_m),

            vx=float(v.north_m_s),
            vy=float(v.east_m_s),
            vz=float(v.down_m_s),

            vehicle_state=vehicle_state,

            localization_valid=bool(
                state.localization_valid
            ),
        )

    async def stabilize_after_takeoff(
        self,
        timeout_s: float = 6.0,
        max_speed_m_s: float = 0.75,
        consecutive_samples: int = 3,
    ) -> None:
        """Hand off Action/Takeoff mode to a stable Hold before NAV Offboard.

        Single Flight may bootstrap from a physical ground spawn with
        ``action.takeoff()``.  PX4 can still be completing the takeoff mode
        transition when NAV-001 immediately requests Offboard.  Mission Runtime
        naturally inserts a Hold post-action between TAKEOFF-001 and NAV-001;
        this helper gives the interactive ground bootstrap the same stable
        hand-off without claiming a TAKEOFF-001 verdict.
        """
        if not self._connected:
            raise RuntimeError(
                "PX4SITLAdapter is not connected"
            )
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if max_speed_m_s <= 0:
            raise ValueError("max_speed_m_s must be > 0")
        if consecutive_samples <= 0:
            raise ValueError("consecutive_samples must be > 0")

        await self._drone.action.hold()
        self._offboard_started = False

        deadline = time.monotonic() + float(timeout_s)
        stable = 0
        last_speed = float("inf")

        while time.monotonic() < deadline:
            state = await self.get_state()
            sample = await self.get_telemetry_sample()
            last_speed = math.sqrt(
                sample.vx * sample.vx
                + sample.vy * sample.vy
                + sample.vz * sample.vz
            )

            if (
                state.connected
                and state.airborne
                and state.localization_valid
                and last_speed <= float(max_speed_m_s)
            ):
                stable += 1
                if stable >= int(consecutive_samples):
                    return
            else:
                stable = 0

            await asyncio.sleep(0.2)

        raise TimeoutError(
            "PX4 SITL takeoff-to-navigation stabilization timeout: "
            f"speed_m_s={last_speed:.3f}"
        )

    async def send_position_target(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float = 0.0,
    ) -> None:
        """Send a LOCAL_NED position target with a conservative Offboard hand-off.

        PX4 requires an Offboard setpoint before ``offboard.start()``.  When
        entering Offboard from Takeoff/Hold, first seed the controller with the
        *current measured position*, start Offboard, then send the destination.
        This avoids a mode-transition race where a far destination setpoint is
        issued while PX4 is still leaving Action/Takeoff mode.
        """

        if not self._connected:
            raise RuntimeError(
                "PX4SITLAdapter is not connected"
            )

        target = PositionNedYaw(
            float(x),
            float(y),
            float(z),
            float(yaw_deg),
        )

        if not self._offboard_started:
            current = await self.get_position()
            seed = PositionNedYaw(
                float(current.x),
                float(current.y),
                float(current.z),
                float(yaw_deg),
            )
            await self._drone.offboard.set_position_ned(seed)
            await self._drone.offboard.start()
            self._offboard_started = True
            # Give PX4 one control-cycle window to enter Offboard before the
            # destination changes from the measured hold point.
            await asyncio.sleep(0.2)

        await self._drone.offboard.set_position_ned(target)

    async def land(
        self,
        timeout_s: float = 30.0,
    ) -> None:
        """Public LAND-001 adapter operation with a stabilized hand-off.

        LAND-001 verifies horizontal drift from the beginning of its frozen
        telemetry window.  Starting PX4 Land while the vehicle still carries
        ReturnHome/NAV momentum can produce metres of lateral drift in Gazebo.
        First enter Hold and wait for measured velocity to settle, then request
        the native PX4 landing.  The acceptance verifier remains unchanged.
        """
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        started = time.monotonic()
        await self.hold_position(
            timeout_s=min(6.0, max(1.0, float(timeout_s) * 0.30)),
            max_speed_m_s=0.30,
            consecutive_samples=3,
        )
        remaining = float(timeout_s) - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("PX4 SITL landing stabilization timeout")
        await self.land_and_wait(timeout_s=remaining)

    async def land_and_wait(
        self,
        timeout_s: float = 30.0,
    ) -> None:
        """Land the PX4 SITL vehicle and wait for measured landed state.

        SIMULATION ONLY: this method is intended for PX4 SITL/Gazebo.
        """
        if not self._connected:
            raise RuntimeError("PX4SITLAdapter is not connected")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        state = await self.get_state()
        if not state.airborne:
            return

        await self._drone.action.land()

        deadline = time.monotonic() + float(timeout_s)

        while time.monotonic() < deadline:
            state = await self.get_state()

            if not state.connected:
                raise RuntimeError(
                    "PX4 SITL connection lost during landing"
                )

            if not state.airborne:
                return

            await asyncio.sleep(0.2)

        raise TimeoutError(
            "PX4 SITL landing timeout"
        )

    async def hold_position(
        self,
        timeout_s: float = 6.0,
        max_speed_m_s: float = 0.30,
        consecutive_samples: int = 3,
    ) -> None:
        """Enter Hold and wait until measured translational speed settles.

        A mode-change acknowledgement is not the same thing as a stationary
        aircraft.  Waiting for several low-speed telemetry samples prevents the
        next interactive leg or LAND-001 window from inheriting significant
        momentum from the previous Offboard target.
        """
        if not self._connected:
            raise RuntimeError("PX4SITLAdapter is not connected")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if max_speed_m_s <= 0:
            raise ValueError("max_speed_m_s must be > 0")
        if consecutive_samples <= 0:
            raise ValueError("consecutive_samples must be > 0")

        try:
            await self._drone.action.hold()
        finally:
            # Hold changes PX4 out of Offboard mode.  Keep our local ownership
            # flag consistent even if MAVSDK reports a transition error.
            self._offboard_started = False

        deadline = time.monotonic() + float(timeout_s)
        stable = 0
        last_speed = float("inf")
        while time.monotonic() < deadline:
            state = await self.get_state()
            if not state.connected:
                raise RuntimeError("PX4 SITL connection lost while entering Hold")
            if not state.airborne:
                return
            if not state.localization_valid:
                stable = 0
                await asyncio.sleep(0.2)
                continue

            sample = await self.get_telemetry_sample()
            last_speed = math.sqrt(
                sample.vx * sample.vx
                + sample.vy * sample.vy
                + sample.vz * sample.vz
            )
            if last_speed <= float(max_speed_m_s):
                stable += 1
                if stable >= int(consecutive_samples):
                    return
            else:
                stable = 0
            await asyncio.sleep(0.2)

        raise TimeoutError(
            "PX4 SITL Hold stabilization timeout: "
            f"speed_m_s={last_speed:.3f}"
        )

    async def abort(self) -> None:
        """
        Stop the adapter's active Offboard session.

        This method intentionally does NOT:
            - land
            - disarm
            - reboot
            - change the simulator

        Higher-level orchestration owns those
        lifecycle decisions.
        """

        if not self._offboard_started:
            return

        try:
            await (
                self._drone
                .offboard
                .stop()
            )

        finally:
            # Even if MAVSDK reports an error while
            # stopping, do not leave the local object
            # claiming that it owns an active session.
            self._offboard_started = False
