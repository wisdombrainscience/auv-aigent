from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Fields that may originate from the human user.
USER_PROVIDED_FIELDS = frozenset(
    {
        "target_position_m",
        "position_tolerance_m",
        "timeout_s",
        "frame",
        "scenario_id",
    }
)


# Fields that MUST come from PX4 / telemetry.
# They are never accepted as user-supplied truth.
SYSTEM_OBSERVED_FIELDS = frozenset(
    {
        "current_position",
        "vehicle_state",
        "airborne_state",
        "localization_valid",
        "connection_state",
    }
)


@dataclass(frozen=True)
class UserRuntimeInput:
    """
    Raw runtime input allowed from the user.

    Semantic validation is implemented later in STEP 17.
    """

    target_position_m: tuple[
        float,
        float,
        float,
    ]

    position_tolerance_m: float
    timeout_s: float

    frame: str = "LOCAL_NED"
    scenario_id: str = "T01"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_position_m": list(
                self.target_position_m
            ),
            "position_tolerance_m":
                self.position_tolerance_m,
            "timeout_s": self.timeout_s,
            "frame": self.frame,
            "scenario_id": self.scenario_id,
        }


@dataclass(frozen=True)
class CanonicalExperimentInput:
    """
    Canonical input stored as input.json.

    task_id is produced by the framework rather
    than treated as observed vehicle state.
    """

    task_id: str

    target_position_m: tuple[
        float,
        float,
        float,
    ]

    position_tolerance_m: float
    timeout_s: float

    frame: str
    scenario_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "frame": self.frame,
            "target_position_m": list(
                self.target_position_m
            ),
            "position_tolerance_m":
                self.position_tolerance_m,
            "timeout_s": self.timeout_s,
            "scenario_id": self.scenario_id,
        }


CANONICAL_INPUT_FIELDS = frozenset(
    {
        "task_id",
        "frame",
        "target_position_m",
        "position_tolerance_m",
        "timeout_s",
        "scenario_id",
    }
)
