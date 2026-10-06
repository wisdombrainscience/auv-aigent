from pathlib import Path

import pytest

from orchestration.web_experiment import Step37AExperimentManager


def base():
    return {
        "start_position_m": [0, 0, -2],
        "target_position_m": [10, 5, -3],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 30,
        "frame": "LOCAL_NED",
        "scenario_id": "STEP37A-TEST",
    }


def test_validate_accepts_canonical_request():
    out = Step37AExperimentManager.validate_request(base())
    assert out["start_position_m"] == [0.0, 0.0, -2.0]
    assert out["target_position_m"] == [10.0, 5.0, -3.0]


@pytest.mark.parametrize("field", ["start_position_m", "target_position_m"])
def test_validate_rejects_wrong_vector(field):
    data = base()
    data[field] = [1, 2]
    with pytest.raises(ValueError):
        Step37AExperimentManager.validate_request(data)


def test_validate_rejects_non_local_ned():
    data = base()
    data["frame"] = "GLOBAL"
    with pytest.raises(ValueError):
        Step37AExperimentManager.validate_request(data)


def test_validate_rejects_nonfinite():
    data = base()
    data["target_position_m"][0] = float("nan")
    with pytest.raises(ValueError):
        Step37AExperimentManager.validate_request(data)


def test_validate_rejects_nonpositive_limits():
    data = base()
    data["timeout_s"] = 0
    with pytest.raises(ValueError):
        Step37AExperimentManager.validate_request(data)


def test_project_path_is_normalized(tmp_path: Path):
    manager = Step37AExperimentManager(tmp_path)
    assert manager.project == tmp_path.resolve()


def test_validate_accepts_ground_spawn_for_single_flight_bootstrap():
    data = base()
    data["start_position_m"] = [0.0, 0.0, 0.0]
    out = Step37AExperimentManager.validate_request(data)
    assert out["start_position_m"] == [0.0, 0.0, 0.0]
    assert out["target_position_m"][2] < 0


def test_validate_rejects_positive_down_start_and_ground_target():
    data = base()
    data["start_position_m"] = [0.0, 0.0, 0.1]
    with pytest.raises(ValueError, match="start Z"):
        Step37AExperimentManager.validate_request(data)

    data = base()
    data["target_position_m"] = [1.0, 1.0, 0.0]
    with pytest.raises(ValueError, match="target Z"):
        Step37AExperimentManager.validate_request(data)
