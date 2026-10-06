from __future__ import annotations

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


class TakeoffModule(BaseCapabilityModule):
    """Deterministic TAKEOFF-001 implementation.

    The capability accepts a positive-up target altitude for operator-facing
    clarity while all measured positions remain LOCAL_NED internally.  The
    independent verifier, not this module, owns the authoritative PASS/FAIL
    verdict.
    """

    def __init__(
        self,
        adapter: UAVAdapter,
        *,
        log_hook=None,
    ) -> None:
        super().__init__(log_hook=log_hook)
        self.adapter = adapter

    async def validate_input(self, input_data: dict[str, Any]) -> None:
        required = {
            "target_altitude_m",
            "altitude_tolerance_m",
            "horizontal_tolerance_m",
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

        try:
            target_altitude = float(input_data["target_altitude_m"])
            altitude_tolerance = float(input_data["altitude_tolerance_m"])
            horizontal_tolerance = float(input_data["horizontal_tolerance_m"])
            timeout = float(input_data["timeout_s"])
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("numeric input invalid") from exc

        values = (
            target_altitude,
            altitude_tolerance,
            horizontal_tolerance,
            timeout,
        )
        if not all(math.isfinite(v) for v in values):
            raise InvalidInputError("numeric values must be finite")
        if target_altitude <= 0:
            raise InvalidInputError("target_altitude_m must be > 0")
        if altitude_tolerance <= 0:
            raise InvalidInputError("altitude_tolerance_m must be > 0")
        if horizontal_tolerance <= 0:
            raise InvalidInputError("horizontal_tolerance_m must be > 0")
        if timeout <= 0:
            raise InvalidInputError("timeout_s must be > 0")

    async def check_preconditions(self, input_data: dict[str, Any]) -> None:
        state = await self.adapter.get_state()
        failures: list[str] = []

        if not state.connected:
            failures.append("connected=false")
        if state.airborne:
            failures.append("airborne=true")
        if not state.localization_valid:
            failures.append("localization_valid=false")

        if failures:
            raise PreconditionFailedError("; ".join(failures))

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        start = await self.adapter.get_position()
        target_altitude = float(input_data["target_altitude_m"])
        timeout = float(input_data["timeout_s"])

        await self.adapter.takeoff_to_altitude(
            target_altitude_m=target_altitude,
            timeout_s=timeout,
        )

        state = await self.adapter.get_state()
        if not state.localization_valid:
            raise LocalizationLostError(
                "Localization became invalid during takeoff"
            )

        final = await self.adapter.get_position()
        return {
            "start_position": {
                "x": start.x,
                "y": start.y,
                "z": start.z,
            },
            "final_position": {
                "x": final.x,
                "y": final.y,
                "z": final.z,
            },
            "target_altitude_m": target_altitude,
            "measured_altitude_m": -float(final.z),
        }

    async def build_result(
        self,
        input_data: dict[str, Any],
        execution_output: Any,
        elapsed_time_s: float,
    ) -> dict[str, Any]:
        start = execution_output["start_position"]
        final = execution_output["final_position"]
        target_altitude = float(execution_output["target_altitude_m"])
        measured_altitude = float(execution_output["measured_altitude_m"])
        horizontal_drift = math.hypot(
            float(final["x"]) - float(start["x"]),
            float(final["y"]) - float(start["y"]),
        )

        return {
            "task_id": input_data.get("task_id"),
            "status": "completed",
            "target_altitude_m": target_altitude,
            "measured_altitude_m": measured_altitude,
            "altitude_error_m": abs(measured_altitude - target_altitude),
            "horizontal_drift_m": horizontal_drift,
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
            "elapsed_time_s": float(elapsed_time_s),
        }
