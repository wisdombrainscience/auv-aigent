import asyncio
import csv
import shutil
from pathlib import Path

from orchestration.web_experiment import ExperimentStore, Step37AExperimentManager
from src.adapters.px4_sitl_adapter import PX4SITLAdapter


class FakeDroneProcess:
    def __init__(self):
        self.stop_calls = 0

    def _stop_mavsdk_server(self):
        self.stop_calls += 1


def test_adapter_close_stops_embedded_mavsdk_server_once():
    adapter = object.__new__(PX4SITLAdapter)
    adapter._closed = False
    adapter._connected = True
    adapter._offboard_started = True
    adapter._last_state = object()
    adapter._drone = FakeDroneProcess()

    asyncio.run(adapter.close())
    asyncio.run(adapter.close())

    assert adapter._drone.stop_calls == 1
    assert adapter._closed is True
    assert adapter._connected is False
    assert adapter._offboard_started is False
    assert adapter._last_state is None


class ConnectFailAdapter:
    instances = []

    def __init__(self):
        self.closed = False
        self.__class__.instances.append(self)

    async def connect(self):
        raise RuntimeError("synthetic_connect_failure")

    async def get_state(self):
        raise RuntimeError("not_connected")

    async def abort(self):
        return None

    async def close(self):
        self.closed = True


def _project(tmp_path: Path) -> Path:
    source_root = Path(__file__).resolve().parents[2]
    for rel in [
        "config/input_policy.yaml",
        "config/experiment.yaml",
        "config/resource_policy.yaml",
        "specs/NAV-001.yaml",
    ]:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / rel, dst)
    src = tmp_path / "src/capabilities/navigation/go_to_target.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / "src/capabilities/navigation/go_to_target.py", src)
    return tmp_path


def test_connect_failure_keeps_original_error_and_finalizable_telemetry(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import src.adapters.px4_sitl_adapter as px4_module

    ConnectFailAdapter.instances.clear()
    monkeypatch.setattr(px4_module, "PX4SITLAdapter", ConnectFailAdapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, -2.0], {"managed": True, "status": "RUNNING"})

    run_id = "connect-fail"
    request = {
        "start_position_m": [0.0, 0.0, -2.0],
        "target_position_m": [1.0, 0.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "CONNECT-FAIL",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})
    manager.session.mark_run_started(run_id, request["target_position_m"])

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "FAIL"
    assert "synthetic_connect_failure" in item["error"]
    assert manager.session.snapshot()["state"] == "RESET_REQUIRED"
    assert ConnectFailAdapter.instances[-1].closed is True

    attempt = Path(item["attempt_dir"])
    telemetry = attempt / "telemetry.csv"
    assert telemetry.is_file()
    with telemetry.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == []
    assert (attempt / "runtime_error.json").is_file()
    assert (attempt / "finalized.json").is_file()
