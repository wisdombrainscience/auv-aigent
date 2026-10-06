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


class LandModule(BaseCapabilityModule):
    """Deterministic LAND-001 implementation.

    The module requests landing and reports measured start/final state.  It does
    not decide acceptance; the independent LAND-001 verifier owns PASS/FAIL.
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
            "ground_altitude_tolerance_m",
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
            ground_tolerance = float(input_data["ground_altitude_tolerance_m"])
            horizontal_tolerance = float(input_data["horizontal_tolerance_m"])
            timeout = float(input_data["timeout_s"])
        except (TypeError, ValueError) as exc:
            raise InvalidInputError("numeric input invalid") from exc

        values = (ground_tolerance, horizontal_tolerance, timeout)
        if not all(math.isfinite(v) for v in values):
            raise InvalidInputError("numeric values must be finite")
        if ground_tolerance <= 0:
            raise InvalidInputError("ground_altitude_tolerance_m must be > 0")
        if horizontal_tolerance <= 0:
            raise InvalidInputError("horizontal_tolerance_m must be > 0")
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

        if failures:
            raise PreconditionFailedError("; ".join(failures))

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        start = await self.adapter.get_position()
        timeout = float(input_data["timeout_s"])

        await self.adapter.land(timeout_s=timeout)

        state = await self.adapter.get_state()
        if not state.localization_valid:
            raise LocalizationLostError(
                "Localization became invalid during landing"
            )

        final = await self.adapter.get_position()
        return {
            "start_position": {"x": start.x, "y": start.y, "z": start.z},
            "final_position": {"x": final.x, "y": final.y, "z": final.z},
            "final_airborne": bool(state.airborne),
        }

    async def build_result(
        self,
        input_data: dict[str, Any],
        execution_output: Any,
        elapsed_time_s: float,
    ) -> dict[str, Any]:
        start = execution_output["start_position"]
        final = execution_output["final_position"]
        final_altitude = -float(final["z"])
        start_altitude = -float(start["z"])
        horizontal_drift = math.hypot(
            float(final["x"]) - float(start["x"]),
            float(final["y"]) - float(start["y"]),
        )

        return {
            "task_id": input_data.get("task_id"),
            "status": "completed",
            "start_position": [
                float(start["x"]), float(start["y"]), float(start["z"])
            ],
            "final_position": [
                float(final["x"]), float(final["y"]), float(final["z"])
            ],
            "start_altitude_m": start_altitude,
            "final_altitude_m": final_altitude,
            "descent_m": start_altitude - final_altitude,
            "horizontal_drift_m": horizontal_drift,
            "final_airborne": bool(execution_output["final_airborne"]),
            "elapsed_time_s": float(elapsed_time_s),
        }
