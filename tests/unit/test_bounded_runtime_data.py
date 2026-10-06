import csv
from pathlib import Path

from orchestration.web_experiment import ExperimentStore, latest_telemetry


def test_experiment_store_is_bounded():
    store = ExperimentStore(max_items=3)
    for i in range(5):
        store.create(str(i), {"n": i})
    assert len(store) == 3
    assert store.get("0") is None
    assert store.get("4")["n"] == 4


def test_latest_telemetry_returns_tail_without_full_history(tmp_path: Path):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    path = attempt / "telemetry.csv"
    fields = ["timestamp", "x", "y", "z", "vx", "vy", "vz", "vehicle_state", "localization_valid"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i in range(1000):
            writer.writerow({
                "timestamp": i,
                "x": i,
                "y": 0,
                "z": -2,
                "vx": 0,
                "vy": 0,
                "vz": 0,
                "vehicle_state": "AIRBORNE",
                "localization_valid": True,
            })
    rows = latest_telemetry(attempt, limit=5)
    assert len(rows) == 5
    assert rows[0]["x"] == "995"
    assert rows[-1]["x"] == "999"
