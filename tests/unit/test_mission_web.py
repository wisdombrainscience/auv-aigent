from pathlib import Path

import pytest

from orchestration.web_mission import MissionWebManager


PROJECT = Path(__file__).resolve().parents[2]


def request() -> dict:
    return {
        "scenario_id": "MISSION-UI-TEST",
        "home_position_m": [0.0, 0.0],
        "takeoff_altitude_m": 3.0,
        "return_altitude_m": 3.0,
        "position_tolerance_m": 0.5,
        "altitude_tolerance_m": 0.5,
        "horizontal_tolerance_m": 1.0,
        "ground_altitude_tolerance_m": 0.5,
        "continuity_tolerance_m": 1.0,
        "terminal_home_tolerance_m": 1.0,
        "timeout_s": 30.0,
        "waypoints": [
            {"north": 5.0, "east": 0.0, "altitude": 3.0},
            {"north": 5.0, "east": 5.0, "altitude": 4.0},
        ],
    }


def test_builder_request_becomes_verified_mission_plan() -> None:
    manager = MissionWebManager(PROJECT)
    canonical = manager.validate_request(request())
    plan = manager.build_mission_plan(canonical)

    assert plan["mission_id"] == "MISSION-001"
    assert [s["capability_id"] for s in plan["steps"]] == [
        "TAKEOFF-001",
        "NAV-001",
        "NAV-001",
        "RETURN-HOME-001",
        "LAND-001",
    ]
    assert plan["steps"][1]["input"]["target_position_m"] == [5.0, 0.0, -3.0]
    assert plan["steps"][2]["input"]["target_position_m"] == [5.0, 5.0, -4.0]
    assert plan["terminal_home_position_m"] == [0.0, 0.0]


def test_builder_rejects_waypoint_outside_canonical_workspace() -> None:
    manager = MissionWebManager(PROJECT)
    raw = request()
    raw["waypoints"] = [{"north": 999.0, "east": 0.0, "altitude": 3.0}]
    with pytest.raises(ValueError, match="outside the permitted simulation region"):
        manager.validate_request(raw)


def test_builder_requires_at_least_one_waypoint() -> None:
    manager = MissionWebManager(PROJECT)
    raw = request()
    raw["waypoints"] = []
    with pytest.raises(ValueError, match="at least one waypoint"):
        manager.validate_request(raw)
