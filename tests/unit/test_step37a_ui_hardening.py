from pathlib import Path


def _html() -> str:
    return (Path(__file__).resolve().parents[2] / "user_interface" / "step37a_console.html").read_text(encoding="utf-8")


def test_ui_uses_separate_live_vehicle_position_and_last_result_labels():
    html = _html()
    assert "current_vehicle_position_m" in html
    assert "Last error" in html
    assert "Last verifier" in html


def test_restart_clears_stale_flight_metrics():
    html = _html()
    assert "function clearFlightMetrics()" in html
    assert "clearFlightMetrics();$('runStatus').textContent='READY'" in html


def test_target_and_uav_labels_have_collision_offsets():
    html = _html()
    assert "Math.hypot(pu[0]-pt[0],pu[1]-pt[1])<42" in html
