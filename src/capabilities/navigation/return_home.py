from __future__ import annotations

import asyncio
import math
from typing import Any

from src.adapters.base_uav_adapter import UAVAdapter
from src.core.base_capability import BaseCapabilityModule
from src.core.errors import (
    InvalidInputError,
    LocalizationLostError,
    PreconditionFailedError,
    UnsupportedFrameError,
)


class ReturnHomeModule(BaseCapabilityModule):
    """Deterministic RETURN-HOME-001 implementation.

    The capability returns an already-airborne vehicle to a declared LOCAL_NED
    home horizontal anchor while maintaining a positive-up return altitude.  It
    intentionally does not land; LAND-001 owns touchdown semantics.

    The module only performs control and reports measured state.  Official
    PASS/FAIL belongs to the independent RETURN-HOME-001 verifier.
    """

    def __init__(
        self,
        adapter: UAVAdapter,
        *,
        poll_interval_s: float = 0.1,
        log_hook=None,
    ) -> None:
        super().__init__(log_hook=log_hook)
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        self.adapter = adapter
        self.poll_interval_s = float(poll_interval_s)

    async def validate_input(self, input_data: dict[str, Any]) -> None:
        required = {
            "home_position_m",
            "return_altitude_m",
            "horizontal_tolerance_m",
            "altitude_tolerance_m",
            "timeout_s",
            "frame",
        }
        missing = required - set(input_data)
        if missing:
            raise InvalidInputError(
                "missing fields: " + ",".join(sorted(missing))
            )

        if input_data["frame"] != "LOCAL_NED":
            raise UnsupportedFrameError(
                f"Unsupported frame: {input_data['frame']}"
            )

        home = input_data["home_position_m"]
        if not isinstance(home, (list, tuple)) or len(home) != 2:
            raise InvalidInputError(
                "home_position_m must contain exactly 2 values [north,east]"
            )

        try:
            home_n, home_e = map(float, home)
            return_altitude = float(input_data["return_altitude_m"])
            horizontal_tolerance = float(input_data["horizontal_tolerance_m"])
            altitude_tolerance = float(input_data["altitude_tolerance_m"])
            timeout = float(input_data["timeout_s"])
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("numeric input invalid") from exc

        values = (
            home_n,
            home_e,
            return_altitude,
            horizontal_tolerance,
            altitude_tolerance,
            timeout,
        )
        if not all(math.isfinite(v) for v in values):
            raise InvalidInputError("numeric values must be finite")
        if return_altitude <= 0:
            raise InvalidInputError("return_altitude_m must be > 0")
        if horizontal_tolerance <= 0:
            raise InvalidInputError("horizontal_tolerance_m must be > 0")
        if altitude_tolerance <= 0:
            raise InvalidInputError("altitude_tolerance_m must be > 0")
        if timeout <= 0:
            raise InvalidInputError("timeout_s must be > 0")

    async def check_preconditions(self, input_data: dict[str, Any]) -> None:
        state = await self.adapter.get_state()
        failures: list[str] = []

        if not state.connected:
            failures.append("connected=false")
        if not state.airborne:
            failures.append("airborne=false")
        if not state.localization_valid:
            failures.append("localization_valid=false")

        if not failures:
            position = await self.adapter.get_position()
            home_n, home_e = (float(v) for v in input_data["home_position_m"])
            horizontal_tolerance = float(input_data["horizontal_tolerance_m"])
            start_home_distance = math.hypot(
                float(position.x) - home_n,
                float(position.y) - home_e,
            )
            if start_home_distance <= horizontal_tolerance:
                failures.append("already_within_home_horizontal_tolerance")

        if failures:
            raise PreconditionFailedError("; ".join(failures))

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        start = await self.adapter.get_position()
        home_n, home_e = (float(v) for v in input_data["home_position_m"])
        return_altitude = float(input_data["return_altitude_m"])
        horizontal_tolerance = float(input_data["horizontal_tolerance_m"])
        altitude_tolerance = float(input_data["altitude_tolerance_m"])

        target_z = -return_altitude
        await self.adapter.send_position_target(
            x=home_n,
            y=home_e,
            z=target_z,
            yaw_deg=0.0,
        )

        # As with NAV-001, use a tighter controller completion envelope than
        # the frozen verifier limits.  This prevents ReturnHome from handing a
        # fast-moving vehicle directly to LAND-001 at the edge of the home
        # acceptance circle.
        control_horizontal_tolerance = min(
            horizontal_tolerance,
            max(0.03, horizontal_tolerance * 0.60),
        )
        control_altitude_tolerance = min(
            altitude_tolerance,
            max(0.03, altitude_tolerance * 0.60),
        )
        settled_samples = 0
        required_settled_samples = 3

        try:
            while True:
                state = await self.adapter.get_state()
                if not state.connected:
                    await self.adapter.abort()
                    raise RuntimeError("UAV connection lost during return-home")
                if not state.localization_valid:
                    await self.adapter.abort()
                    raise LocalizationLostError(
                        "Localization became invalid during return-home"
                    )
                if not state.airborne:
                    await self.adapter.abort()
                    raise RuntimeError("UAV became non-airborne during return-home")

                position = await self.adapter.get_position()
                horizontal_error = math.hypot(
                    float(position.x) - home_n,
                    float(position.y) - home_e,
                )
                altitude_error = abs((-float(position.z)) - return_altitude)

                if (
                    horizontal_error <= control_horizontal_tolerance
                    and altitude_error <= control_altitude_tolerance
                ):
                    settled_samples += 1
                else:
                    settled_samples = 0

                if settled_samples >= required_settled_samples:
                    return {
                        "start_position": {
                            "x": start.x,
                            "y": start.y,
                            "z": start.z,
                        },
                        "final_position": {
                            "x": position.x,
                            "y": position.y,
                            "z": position.z,
                        },
                        "home_position_m": [home_n, home_e],
                        "return_altitude_m": return_altitude,
                        "horizontal_error_m": horizontal_error,
                        "altitude_error_m": altitude_error,
                    }

                await asyncio.sleep(self.poll_interval_s)

        except asyncio.CancelledError:
            await self.adapter.abort()
            raise
        except Exception:
            await self.adapter.abort()
            raise

    async def build_result(
        self,
        input_data: dict[str, Any],
        execution_output: Any,
        elapsed_time_s: float,
    ) -> dict[str, Any]:
        start = execution_output["start_position"]
        final = execution_output["final_position"]
        home = execution_output["home_position_m"]

        return {
            "task_id": input_data.get("task_id"),
            "status": "completed",
            "home_position_m": [float(home[0]), float(home[1])],
            "return_altitude_m": float(execution_output["return_altitude_m"]),
            "start_position": [
                float(start["x"]),
                float(start["y"]),
                float(start["z"]),
            ],
            "final_position": [
                float(final["x"]),
                float(final["y"]),
                float(final["z"]),
            ],
            "horizontal_error_m": float(execution_output["horizontal_error_m"]),
            "altitude_error_m": float(execution_output["altitude_error_m"]),
            "elapsed_time_s": float(elapsed_time_s),
        }
