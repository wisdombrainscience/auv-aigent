from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from user_interface.input_schema import (
    SYSTEM_OBSERVED_FIELDS,
    USER_PROVIDED_FIELDS,
    UserRuntimeInput,
)


@dataclass(frozen=True)
class TestAreaBounds:
    # Prevent pytest from treating this domain model
    # as a test class merely because its name starts
    # with "Test".
    __test__ = False
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    def __post_init__(self) -> None:
        values = (
            self.x_min,
            self.x_max,
            self.y_min,
            self.y_max,
            self.z_min,
            self.z_max,
        )

        if not all(
            math.isfinite(float(v))
            for v in values
        ):
            raise ValueError(
                "Test-area bounds must be finite"
            )

        if self.x_min > self.x_max:
            raise ValueError("x_min > x_max")

        if self.y_min > self.y_max:
            raise ValueError("y_min > y_max")

        if self.z_min > self.z_max:
            raise ValueError("z_min > z_max")

    def contains(
        self,
        x: float,
        y: float,
        z: float,
    ) -> bool:
        return (
            self.x_min <= x <= self.x_max
            and self.y_min <= y <= self.y_max
            and self.z_min <= z <= self.z_max
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "z_min": self.z_min,
            "z_max": self.z_max,
        }


@dataclass(frozen=True)
class InputValidationResult:
    valid: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "failure_code": (
                None
                if self.valid
                else "INVALID_INPUT"
            ),
            "errors": list(self.errors),
        }


class RuntimeInputValidator:
    def __init__(
        self,
        *,
        supported_frames: set[str]
        | frozenset[str]
        | None = None,
        test_area: TestAreaBounds | None = None,
    ) -> None:
        # Current PoC implementation uses LOCAL_NED.
        self.supported_frames = frozenset(
            supported_frames
            or {"LOCAL_NED"}
        )

        self.test_area = test_area

    def validate_raw_mapping(
        self,
        raw: Mapping[str, Any],
    ) -> InputValidationResult:
        errors: list[str] = []

        required = {
            "target_position_m",
            "position_tolerance_m",
            "timeout_s",
        }

        missing = required - set(raw)

        if missing:
            errors.append(
                "missing_fields:"
                + ",".join(sorted(missing))
            )

        spoofed = (
            set(raw)
            & SYSTEM_OBSERVED_FIELDS
        )

        if spoofed:
            errors.append(
                "system_state_spoof:"
                + ",".join(sorted(spoofed))
            )

        unknown = (
            set(raw)
            - USER_PROVIDED_FIELDS
            - SYSTEM_OBSERVED_FIELDS
        )

        if unknown:
            errors.append(
                "unsupported_fields:"
                + ",".join(sorted(unknown))
            )

        return InputValidationResult(
            valid=not errors,
            errors=tuple(errors),
        )

    def validate(
        self,
        value: UserRuntimeInput,
    ) -> InputValidationResult:
        errors: list[str] = []

        target = value.target_position_m

        if len(target) != 3:
            errors.append(
                "target_position_m_must_have_3_values"
            )
            return InputValidationResult(
                valid=False,
                errors=tuple(errors),
            )

        try:
            x, y, z = (
                float(target[0]),
                float(target[1]),
                float(target[2]),
            )

            tolerance = float(
                value.position_tolerance_m
            )

            timeout = float(
                value.timeout_s
            )

        except (TypeError, ValueError):
            return InputValidationResult(
                valid=False,
                errors=("numeric_type_invalid",),
            )

        numeric_values = (
            x,
            y,
            z,
            tolerance,
            timeout,
        )

        if not all(
            math.isfinite(v)
            for v in numeric_values
        ):
            errors.append(
                "non_finite_numeric_value"
            )

        if tolerance <= 0:
            errors.append(
                "position_tolerance_m_must_be_positive"
            )

        if timeout <= 0:
            errors.append(
                "timeout_s_must_be_positive"
            )

        if (
            value.frame
            not in self.supported_frames
        ):
            errors.append(
                f"unsupported_frame:{value.frame}"
            )

        if self.test_area is None:
            errors.append(
                "test_area_not_configured"
            )

        elif all(
            math.isfinite(v)
            for v in (x, y, z)
        ):
            if not self.test_area.contains(
                x,
                y,
                z,
            ):
                errors.append(
                    "target_outside_permitted_test_region"
                )

        return InputValidationResult(
            valid=not errors,
            errors=tuple(errors),
        )
