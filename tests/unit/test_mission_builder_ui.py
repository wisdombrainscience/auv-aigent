from pathlib import Path


def _html() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "user_interface"
        / "mission_builder.html"
    ).read_text(encoding="utf-8")


def test_mission_builder_uses_api_and_waypoint_map() -> None:
    html = _html()
    assert "/api/mission/start" in html
    assert "Click bản đồ để thêm waypoint" in html
    assert "RETURN-HOME-001" in html
    assert "LAND-001" in html


def test_mission_builder_exposes_verifier_results_not_ui_verdicts() -> None:
    html = _html()
    assert "status.result?.checks" in html
    assert "MissionVerifier" not in html  # UI does not implement verifier logic.


def test_mission_terminal_failure_label_is_not_stuck_on_preparing():
    assert "Mission thất bại" in _html()
