from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.mission.model import MissionPlan, REFERENCE_SEQUENCE


def load_reference() -> dict:
    project = Path(__file__).resolve().parents[2]
    return json.loads(
        (project / "config/examples/mission_reference.json").read_text(encoding="utf-8")
    )


def test_reference_mission_has_expected_reference_sequence() -> None:
    plan = MissionPlan.from_dict(load_reference())
    assert tuple(step.capability_id for step in plan.steps) == REFERENCE_SEQUENCE
    assert plan.frame == "LOCAL_NED"
    assert plan.continuity_tolerance_m == 1.0
    assert plan.terminal_home_position_m == (0.0, 0.0)


def test_mission_rejects_non_takeoff_first_step() -> None:
    raw = load_reference()
    raw["steps"][0], raw["steps"][1] = raw["steps"][1], raw["steps"][0]
    with pytest.raises(ValueError, match="first capability"):
        MissionPlan.from_dict(raw)


def test_mission_rejects_non_land_final_step() -> None:
    raw = load_reference()
    raw["steps"] = raw["steps"][:-1]
    with pytest.raises(ValueError, match="final capability"):
        MissionPlan.from_dict(raw)
