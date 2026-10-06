from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


REFERENCE_SEQUENCE = (
    "TAKEOFF-001",
    "NAV-001",
    "RETURN-HOME-001",
    "LAND-001",
)


@dataclass(frozen=True)
class MissionStep:
    step_id: str
    capability_id: str
    input_data: dict[str, Any]
    post_action: str = "none"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MissionStep":
        if not isinstance(raw, dict):
            raise ValueError("mission step must be an object")
        step_id = str(raw.get("step_id") or "").strip()
        capability_id = str(raw.get("capability_id") or "").strip()
        input_data = raw.get("input")
        post_action = str(raw.get("post_action") or "none").strip().lower()
        if not step_id:
            raise ValueError("mission step_id is required")
        if not capability_id:
            raise ValueError(f"mission step {step_id}: capability_id is required")
        if not isinstance(input_data, dict):
            raise ValueError(f"mission step {step_id}: input must be an object")
        if post_action not in {"none", "hold", "land"}:
            raise ValueError(
                f"mission step {step_id}: unsupported post_action {post_action!r}"
            )
        return cls(
            step_id=step_id,
            capability_id=capability_id,
            input_data=dict(input_data),
            post_action=post_action,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "input": dict(self.input_data),
            "post_action": self.post_action,
        }


@dataclass(frozen=True)
class MissionPlan:
    mission_id: str
    frame: str
    continuity_tolerance_m: float
    terminal_home_position_m: tuple[float, float]
    terminal_home_tolerance_m: float
    steps: tuple[MissionStep, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MissionPlan":
        if not isinstance(raw, dict):
            raise ValueError("mission file must contain one JSON object")
        mission_id = str(raw.get("mission_id") or "").strip()
        frame = str(raw.get("frame") or "").strip()
        continuity = float(raw.get("continuity_tolerance_m", 1.0))
        terminal_home = raw.get("terminal_home_position_m")
        terminal_home_tolerance = float(raw.get("terminal_home_tolerance_m", 1.0))
        steps_raw = raw.get("steps")

        if mission_id != "MISSION-001":
            raise ValueError("MISSION-001 is the only supported mission definition")
        if frame != "LOCAL_NED":
            raise ValueError("mission frame must be LOCAL_NED")
        if not math.isfinite(continuity) or continuity <= 0:
            raise ValueError("continuity_tolerance_m must be finite and > 0")
        if not isinstance(terminal_home, (list, tuple)) or len(terminal_home) != 2:
            raise ValueError("terminal_home_position_m must be [north,east]")
        home_n, home_e = (float(v) for v in terminal_home)
        if not all(math.isfinite(v) for v in (home_n, home_e, terminal_home_tolerance)):
            raise ValueError("terminal home values must be finite")
        if terminal_home_tolerance <= 0:
            raise ValueError("terminal_home_tolerance_m must be > 0")
        if not isinstance(steps_raw, list) or len(steps_raw) < 2:
            raise ValueError("mission steps must contain at least 2 steps")

        steps = tuple(MissionStep.from_dict(item) for item in steps_raw)
        if steps[0].capability_id != "TAKEOFF-001":
            raise ValueError("MISSION-001 first capability must be TAKEOFF-001")
        if steps[-1].capability_id != "LAND-001":
            raise ValueError("MISSION-001 final capability must be LAND-001")

        step_ids = [step.step_id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("mission step_id values must be unique")
        for step in steps:
            task_id = step.input_data.get("task_id")
            if task_id not in {None, step.capability_id}:
                raise ValueError(
                    f"mission step {step.step_id}: input.task_id must match capability_id"
                )
            step_frame = step.input_data.get("frame")
            if step_frame not in {None, frame}:
                raise ValueError(
                    f"mission step {step.step_id}: input.frame must match mission frame"
                )

        return cls(
            mission_id=mission_id,
            frame=frame,
            continuity_tolerance_m=continuity,
            terminal_home_position_m=(home_n, home_e),
            terminal_home_tolerance_m=terminal_home_tolerance,
            steps=steps,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "frame": self.frame,
            "continuity_tolerance_m": self.continuity_tolerance_m,
            "terminal_home_position_m": list(self.terminal_home_position_m),
            "terminal_home_tolerance_m": self.terminal_home_tolerance_m,
            "steps": [step.to_dict() for step in self.steps],
        }
