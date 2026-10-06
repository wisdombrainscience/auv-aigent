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


class GoToTargetModule(BaseCapabilityModule):
    """
    Manual known-good NAV-001 baseline.

    This is the human-written control implementation.
    It is intentionally simple.

    Official PASS/FAIL still belongs to the
    independent verifier.
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
            raise ValueError(
                "poll_interval_s must be > 0"
            )

        self.adapter = adapter
        self.poll_interval_s = poll_interval_s

    async def validate_input(
        self,
        input_data: dict[str, Any],
    ) -> None:
        required = {
            "target_position_m",
            "position_tolerance_m",
            "timeout_s",
            "frame",
        }

        missing = required - set(input_data)

        if missing:
            raise InvalidInputError(
                "missing fields: "
                + ",".join(sorted(missing))
            )

        if input_data["frame"] != "LOCAL_NED":
            raise UnsupportedFrameError(
                f"Unsupported frame: "
                f"{input_data['frame']}"
            )

        target = input_data[
            "target_position_m"
        ]

        if (
            not isinstance(target, list)
            and not isinstance(target, tuple)
        ):
            raise InvalidInputError(
                "target_position_m must be a sequence"
            )

        if len(target) != 3:
            raise InvalidInputError(
                "target_position_m must contain "
                "exactly 3 values"
            )

        try:
            x, y, z = map(float, target)

            tolerance = float(
                input_data[
                    "position_tolerance_m"
                ]
            )

            timeout = float(
                input_data["timeout_s"]
            )

        except (TypeError, ValueError) as exc:
            raise InvalidInputError(
                "numeric input invalid"
            ) from exc

        if not all(
            math.isfinite(v)
            for v in (
                x,
                y,
                z,
                tolerance,
                timeout,
            )
        ):
            raise InvalidInputError(
                "numeric values must be finite"
            )

        if tolerance <= 0:
            raise InvalidInputError(
                "position_tolerance_m must be > 0"
            )

        if timeout <= 0:
            raise InvalidInputError(
                "timeout_s must be > 0"
            )

    async def check_preconditions(
        self,
        input_data: dict[str, Any],
    ) -> None:
        state = await self.adapter.get_state()

        failures: list[str] = []

        if not state.connected:
            failures.append("connected=false")

        if not state.airborne:
            failures.append("airborne=false")

        if not state.localization_valid:
            failures.append(
                "localization_valid=false"
            )

        if failures:
            raise PreconditionFailedError(
                "; ".join(failures)
            )

    @staticmethod
    def _position_error(
        current,
        target: tuple[
            float,
            float,
            float,
        ],
    ) -> float:
        dx = current.x - target[0]
        dy = current.y - target[1]
        dz = current.z - target[2]

        return math.sqrt(
            dx * dx
            + dy * dy
            + dz * dz
        )

    async def execute(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        target = tuple(
            float(v)
            for v in input_data[
                "target_position_m"
            ]
        )

        tolerance = float(
            input_data[
                "position_tolerance_m"
            ]
        )

        # The adapter starts/maintains PX4 offboard.
        await self.adapter.send_position_target(
            x=target[0],
            y=target[1],
            z=target[2],
            yaw_deg=0.0,
        )

        try:
            while True:
                state = (
                    await self.adapter.get_state()
                )

                if not state.localization_valid:
                    await self.adapter.abort()

                    raise LocalizationLostError(
                        "Localization became invalid "
                        "during execution"
                    )

                position = (
                    await self.adapter.get_position()
                )

                error = self._position_error(
                    position,
                    target,
                )

                if error <= tolerance:
                    # Capture the actual measured
                    # position before stopping offboard.
                    result = {
                        "final_position": {
                            "x": position.x,
                            "y": position.y,
                            "z": position.z,
                        },
                        "position_error_m":
                            error,
                    }

                    # Success: keep the target active.
                    # The experiment orchestrator performs
                    # cleanup after terminal telemetry has
                    # been captured.
                    return result

                await asyncio.sleep(
                    self.poll_interval_s
                )

        except asyncio.CancelledError:
            # BaseCapabilityModule uses wait_for();
            # timeout cancellation must lead to abort.
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
        return {
            "target_position_m":
                input_data[
                    "target_position_m"
                ],
            "final_position":
                execution_output[
                    "final_position"
                ],
            "position_error_m":
                execution_output[
                    "position_error_m"
                ],
        }
