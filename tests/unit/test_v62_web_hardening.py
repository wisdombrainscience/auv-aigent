from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def test_single_flight_browser_does_not_own_start_position() -> None:
    html = (PROJECT / "user_interface/step37a_console.html").read_text(encoding="utf-8")
    assert "const payload={target_position_m:target" in html
    assert "start_position_m:start" not in html
    server = (PROJECT / "tools/step37a_console.py").read_text(encoding="utf-8")
    assert "owned['start_position_m'] = list(server_start)" in server


def test_mission_builder_exposes_live_progress_and_gazebo_viewer() -> None:
    html = (PROJECT / "user_interface/mission_builder.html").read_text(encoding="utf-8")
    assert "progress_percent" in html
    assert "live_position_m" in html
    assert "trajectory" in html
    assert "/api/simulation/gazebo" in html
    assert "Open Gazebo" in html


def test_single_flight_ground_bootstrap_is_explained_and_phase_visible() -> None:
    html = (PROJECT / "user_interface/step37a_console.html").read_text(encoding="utf-8")
    assert "auto-takeoff" in html
    assert "AUTO_TAKEOFF_SETUP:'AUTO TAKEOFF'" in html
    assert "verdict NAV-001" in html


def test_single_flight_shows_takeoff_to_nav_handoff_phase():
    html = (PROJECT / 'user_interface/step37a_console.html').read_text(encoding='utf-8')
    assert "TAKEOFF_STABILIZE:'STABILIZING'" in html


def test_single_flight_server_auto_continues_after_verified_pass() -> None:
    server = (PROJECT / "orchestration/web_experiment.py").read_text(encoding="utf-8")
    assert 'if session.get("state") == "COMPLETED":' in server
    assert 'session = self.continue_session(target)' in server
    assert 'session.get("last_verdict") != "PASS"' in server
    assert 'session.get("mode") == "LANDED"' in server
