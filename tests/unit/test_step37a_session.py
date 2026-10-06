from pathlib import Path

from orchestration.step37a_session import SessionError, Step37ASession
from orchestration.step37a_simulation import SimulationSupervisor


def test_new_session_requires_reset(tmp_path: Path):
    s = Step37ASession(tmp_path)
    assert s.snapshot()["state"] == "NEW"
    try:
        s.prepare_continue([1, 2, -2])
    except SessionError:
        pass
    else:
        raise AssertionError("continue must be blocked without a session")


def test_reset_then_continue_uses_last_final(tmp_path: Path):
    s = Step37ASession(tmp_path)
    state = s.reset([1.0, 2.0, -3.0], {"managed": True})
    assert state["state"] == "READY"
    s.mark_run_started("run-1", [4.0, 5.0, -3.0])
    s.mark_run_completed("run-1", [4.01, 5.02, -3.01], "PASS")
    state = s.prepare_continue([8.0, 9.0, -3.0])
    assert state["mode"] == "CONTINUE"
    assert state["current_requested_start_position_m"] == [4.01, 5.02, -3.01]
    assert state["current_target_position_m"] == [8.0, 9.0, -3.0]


def test_ned_to_gazebo_pose_mapping():
    pose = SimulationSupervisor.ned_to_gazebo_pose([3.0, 5.0, -2.0])
    assert pose == "5.000000,3.000000,2.000000,0,0,0"


def test_mark_landed_preserves_navigation_origin(tmp_path: Path):
    s = Step37ASession(tmp_path)
    s.reset([0.0, 0.0, -2.0], {"managed": True})
    s.mark_run_started("run-1", [2.0, 0.0, -2.0])
    s.mark_run_completed("run-1", [1.95, 0.02, -2.01], "PASS")
    s.mark_landed()
    state = s.snapshot()
    assert state["state"] == "LANDED"
    assert state["current_requested_start_position_m"] == [1.95, 0.02, -2.01]
    assert state["home_position_m"] == [0.0, 0.0, -2.0]


def test_sync_measured_start_updates_active_run_without_rewriting_previous_final(tmp_path: Path):
    s = Step37ASession(tmp_path)
    s.reset([0.0, 0.0, -2.0], {"managed": True})
    s.mark_run_started("run-1", [1.0, 0.0, -2.0])
    s.mark_run_completed("run-1", [0.9, 0.0, -2.0], "PASS")
    s.prepare_continue([2.0, 0.0, -2.0])
    s.mark_run_started("run-2", [2.0, 0.0, -2.0])
    s.sync_measured_start("run-2", [1.2, 0.1, -2.0])
    state = s.snapshot()
    assert state["current_requested_start_position_m"] == [1.2, 0.1, -2.0]
    assert state["last_verified_final_position_m"] == [0.9, 0.0, -2.0]


def test_mark_landed_refreshes_live_vehicle_position_but_keeps_airborne_anchor(tmp_path: Path):
    s = Step37ASession(tmp_path)
    s.reset([0.0, 0.0, -2.0], {"managed": True})
    s.mark_run_started("run-1", [2.0, 3.0, -3.0])
    s.mark_run_completed("run-1", [2.1, 3.1, -3.02], "PASS")
    s.mark_landed([2.16, 3.08, -0.03])
    state = s.snapshot()
    assert state["state"] == "LANDED"
    assert state["current_vehicle_position_m"] == [2.16, 3.08, -0.03]
    assert state["current_requested_start_position_m"] == [2.1, 3.1, -3.02]
    assert state["last_verified_final_position_m"] == [2.1, 3.1, -3.02]


def test_reset_initializes_live_vehicle_position(tmp_path: Path):
    s = Step37ASession(tmp_path)
    state = s.reset([4.0, 5.0, -2.0], {"managed": True})
    assert state["current_vehicle_position_m"] == [4.0, 5.0, -2.0]


def test_completed_session_separates_verified_final_from_post_hold_live_position(tmp_path: Path):
    s = Step37ASession(tmp_path)
    s.reset([0.0, 0.0, -2.0], {"managed": True})
    s.mark_run_started("run-1", [2.0, 0.0, -2.0])
    s.mark_run_completed(
        "run-1",
        [1.98, 0.01, -2.00],
        "PASS",
        current_position_m=[2.08, 0.06, -1.99],
    )
    state = s.snapshot()
    assert state["last_verified_final_position_m"] == [1.98, 0.01, -2.00]
    assert state["current_requested_start_position_m"] == [1.98, 0.01, -2.00]
    assert state["current_vehicle_position_m"] == [2.08, 0.06, -1.99]
