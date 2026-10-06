import asyncio
import csv
import shutil
from pathlib import Path

from orchestration.web_experiment import ExperimentStore, Step37AExperimentManager
from src.adapters.base_uav_adapter import UAVPosition, UAVState
from src.telemetry.recorder import TelemetrySample
from src.verification.goto_target_verifier import GoToTargetVerifier


class FakePX4Adapter:
    instances = []
    initial_position = [0.0, 0.0, -2.0]

    def __init__(self):
        self.position = list(self.__class__.initial_position)
        self.t = 100.0
        self.aborted = False
        self.landed = False
        self.__class__.instances.append(self)

    async def connect(self):
        return None

    async def prepare_airborne(self, target_down_m: float, timeout_s: float = 30.0):
        self.position[2] = target_down_m

    async def get_state(self):
        await asyncio.sleep(0)
        return UAVState(connected=True, armed=True, airborne=True, localization_valid=True)

    async def get_position(self):
        return UAVPosition(*self.position)

    async def send_position_target(self, x, y, z, yaw_deg=0.0):
        self.position = [float(x), float(y), float(z)]
        # Let the recorder observe the terminal navigation position before the
        # capability completes and closes its execution window.
        await asyncio.sleep(0.12)

    async def get_telemetry_sample(self):
        self.t += 0.1
        return TelemetrySample(
            timestamp_s=self.t,
            x=self.position[0],
            y=self.position[1],
            z=self.position[2],
            vx=0.0,
            vy=0.0,
            vz=0.0,
            vehicle_state="AIRBORNE",
            localization_valid=True,
        )

    async def hold_position(self):
        # Deliberately move the fake vehicle AFTER verification. A broken
        # recorder ordering would append this post-flight state to telemetry.
        self.position = [99.0, 99.0, 0.0]

    async def abort(self):
        self.aborted = True

    async def land_and_wait(self, timeout_s=30.0):
        self.landed = True
        self.position[2] = 0.0


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


def test_finalized_telemetry_is_the_same_dataset_the_verifier_used(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import src.adapters.px4_sitl_adapter as px4_module

    FakePX4Adapter.instances.clear()
    FakePX4Adapter.initial_position = [0.0, 0.0, -2.0]
    monkeypatch.setattr(px4_module, "PX4SITLAdapter", FakePX4Adapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, -2.0], {"managed": True, "status": "RUNNING"})
    run_id = "boundary-regression"
    request = {
        "start_position_m": [0.0, 0.0, -2.0],
        "target_position_m": [1.0, 0.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "BOUNDARY-REGRESSION",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "PASS"
    attempt = Path(item["attempt_dir"])
    original_error = item["result"]["position_error_m"]

    with (attempt / "telemetry.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert float(rows[-1]["x"]) < 2.0  # abort's x=99 was not recorded

    rerun = GoToTargetVerifier(project / "specs/NAV-001.yaml").verify_attempt(
        attempt, write_result=False
    )
    assert rerun.overall == "PASS"
    assert rerun.position_error_m == original_error
    assert FakePX4Adapter.instances[-1].landed is False  # success keeps session alive


def test_continue_resyncs_to_fresh_measured_position_instead_of_failing_start_gate(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import src.adapters.px4_sitl_adapter as px4_module

    FakePX4Adapter.instances.clear()
    # More than the legacy 0.75 m hard start tolerance away from the previous
    # verified final position.  CONTINUE must adopt fresh telemetry instead of
    # producing telemetry_empty / RESET_REQUIRED.
    FakePX4Adapter.initial_position = [1.2, 0.0, -2.0]
    monkeypatch.setattr(px4_module, "PX4SITLAdapter", FakePX4Adapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, -2.0], {"managed": True, "status": "RUNNING"})
    manager.session.mark_run_started("previous", [0.0, 0.0, -2.0])
    manager.session.mark_run_completed("previous", [0.0, 0.0, -2.0], "PASS")
    manager.session.prepare_continue([2.0, 0.0, -2.0])

    run_id = "continue-resync"
    request = {
        "start_position_m": [0.0, 0.0, -2.0],
        "target_position_m": [2.0, 0.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "CONTINUE-RESYNC",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})
    manager.session.mark_run_started(run_id, request["target_position_m"])

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "PASS"
    assert item["start_error_m"] > request["start_position_tolerance_m"]
    assert item["effective_start_position_m"] == [1.2, 0.0, -2.0]
    attempt = Path(item["attempt_dir"])
    gate = __import__("json").loads((attempt / "start_gate_result.json").read_text())
    assert gate["overall"] == "PASS"
    assert gate["reason"] == "continue_resynced_to_measured_start"
    assert manager.session.snapshot()["state"] == "COMPLETED"


class FakeSetupDeniedAdapter(FakePX4Adapter):
    async def prepare_airborne(self, target_down_m: float, timeout_s: float = 30.0):
        raise RuntimeError("synthetic setup failure")


def test_simulation_setup_failure_is_not_misclassified_as_empty_telemetry(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import json
    import src.adapters.px4_sitl_adapter as px4_module

    monkeypatch.setattr(px4_module, "PX4SITLAdapter", FakeSetupDeniedAdapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, -2.0], {"managed": True, "status": "RUNNING"})

    run_id = "setup-failure-classification"
    request = {
        "start_position_m": [0.0, 0.0, -2.0],
        "target_position_m": [1.0, 0.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "SETUP-FAILURE",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})
    manager.session.mark_run_started(run_id, request["target_position_m"])

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "FAIL"
    assert item["result"]["reason"] == "precondition_failed:simulation_setup_error"
    assert item["result"]["checks"]["telemetry"] == "NOT_RUN"
    assert manager.session.snapshot()["state"] == "RESET_REQUIRED"

    attempt = Path(item["attempt_dir"])
    module_output = json.loads((attempt / "module_output.json").read_text())
    verification = json.loads((attempt / "verification.json").read_text())
    runtime_error = json.loads((attempt / "runtime_error.json").read_text())
    telemetry_lines = (attempt / "telemetry.csv").read_text().splitlines()

    assert module_output["status"] == "not_started"
    assert verification["reason"] == "precondition_failed:simulation_setup_error"
    assert runtime_error["message"] == "synthetic setup failure"
    assert telemetry_lines == ["timestamp,x,y,z,vx,vy,vz,vehicle_state,localization_valid"]


def test_ground_spawn_bootstraps_airborne_before_nav_evidence(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import json
    import src.adapters.px4_sitl_adapter as px4_module

    FakePX4Adapter.instances.clear()
    FakePX4Adapter.initial_position = [0.0, 0.0, 0.0]
    monkeypatch.setattr(px4_module, "PX4SITLAdapter", FakePX4Adapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, 0.0], {"managed": True, "status": "RUNNING"})

    run_id = "ground-bootstrap"
    request = {
        "start_position_m": [0.0, 0.0, 0.0],
        "target_position_m": [1.0, 0.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "GROUND-BOOTSTRAP",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})
    manager.session.mark_run_started(run_id, request["target_position_m"])

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "PASS"
    assert item["auto_takeoff_bootstrap"] is True
    assert item["effective_start_position_m"] == [0.0, 0.0, -2.0]
    attempt = Path(item["attempt_dir"])
    gate = json.loads((attempt / "start_gate_result.json").read_text())
    assert gate["overall"] == "PASS"
    assert gate["reason"] == "ground_bootstrap_resynced_to_measured_airborne_start"
    assert gate["requested_ground_position_m"] == [0.0, 0.0, 0.0]
    assert gate["expected_airborne_position_m"] == [0.0, 0.0, -2.0]
    # Capability evidence starts after setup, so NAV telemetry is airborne-only.
    with (attempt / "telemetry.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert all(float(row["z"]) < 0 for row in rows)
    assert manager.session.snapshot()["state"] == "COMPLETED"


def test_ground_bootstrap_drift_fails_as_precondition_not_empty_telemetry(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import json
    import src.adapters.px4_sitl_adapter as px4_module

    FakePX4Adapter.instances.clear()
    FakePX4Adapter.initial_position = [2.0, 0.0, 0.0]
    monkeypatch.setattr(px4_module, "PX4SITLAdapter", FakePX4Adapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, 0.0], {"managed": True, "status": "RUNNING"})

    run_id = "ground-bootstrap-drift"
    request = {
        "start_position_m": [0.0, 0.0, 0.0],
        "target_position_m": [3.0, 0.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "GROUND-BOOTSTRAP-DRIFT",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})
    manager.session.mark_run_started(run_id, request["target_position_m"])

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "FAIL"
    assert item["phase"] == "PRECONDITION_FAILED"
    assert item["result"]["reason"] == "precondition_failed:ground_bootstrap_outside_airborne_start_tolerance"
    assert item["result"]["checks"]["telemetry"] == "NOT_RUN"
    attempt = Path(item["attempt_dir"])
    verification = json.loads((attempt / "verification.json").read_text())
    assert verification["reason"] == item["result"]["reason"]
    telemetry_lines = (attempt / "telemetry.csv").read_text().splitlines()
    assert telemetry_lines == ["timestamp,x,y,z,vx,vy,vz,vehicle_state,localization_valid"]


class FakeGroundHandoffAdapter(FakePX4Adapter):
    async def stabilize_after_takeoff(self, timeout_s: float = 6.0):
        self.stabilized_after_takeoff = True

    async def send_position_target(self, x, y, z, yaw_deg=0.0):
        assert getattr(self, "stabilized_after_takeoff", False) is True
        await super().send_position_target(x, y, z, yaw_deg)


def test_ground_bootstrap_stabilizes_before_nav_execution(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import json
    import src.adapters.px4_sitl_adapter as px4_module

    FakeGroundHandoffAdapter.instances.clear()
    FakeGroundHandoffAdapter.initial_position = [0.0, 0.0, 0.0]
    monkeypatch.setattr(px4_module, "PX4SITLAdapter", FakeGroundHandoffAdapter)

    store = ExperimentStore()
    manager = Step37AExperimentManager(project, store)
    manager.session.reset([0.0, 0.0, 0.0], {"managed": True, "status": "RUNNING"})

    run_id = "ground-bootstrap-handoff"
    request = {
        "start_position_m": [0.0, 0.0, 0.0],
        "target_position_m": [4.0, 3.0, -2.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 5.0,
        "frame": "LOCAL_NED",
        "scenario_id": "GROUND-HANDOFF",
    }
    store.create(run_id, {"run_id": run_id, "status": "RUNNING", "request": request})
    manager.session.mark_run_started(run_id, request["target_position_m"])

    asyncio.run(manager._run(run_id, request))

    item = store.get(run_id)
    assert item["status"] == "PASS"
    adapter = FakeGroundHandoffAdapter.instances[-1]
    assert adapter.stabilized_after_takeoff is True
    attempt = Path(item["attempt_dir"])
    events = [json.loads(line) for line in (attempt / "events.jsonl").read_text().splitlines()]
    kinds = [event["type"] for event in events]
    assert "takeoff_navigation_handoff_started" in kinds
    assert "takeoff_navigation_handoff_completed" in kinds


class _AlwaysRunningSimulation:
    def is_running(self):
        return True


class _NoopThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        return None


def test_start_automatically_enters_continue_after_previous_pass(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import orchestration.web_experiment as web_module

    monkeypatch.setattr(web_module.threading, "Thread", _NoopThread)
    store = ExperimentStore()
    manager = Step37AExperimentManager(
        project,
        store,
        simulation=_AlwaysRunningSimulation(),
    )
    manager.session.reset([0.0, 0.0, -2.0], {"managed": True, "status": "RUNNING"})
    manager.session.mark_run_started("previous", [1.0, 0.0, -2.0])
    manager.session.mark_run_completed(
        "previous",
        [0.95, 0.0, -2.0],
        "PASS",
        current_position_m=[1.30, 0.0, -2.0],
    )

    run_id = manager.start(
        {
            "target_position_m": [3.0, 0.0, -2.0],
            "position_tolerance_m": 0.5,
            "start_position_tolerance_m": 0.75,
            "timeout_s": 5.0,
            "frame": "LOCAL_NED",
            "scenario_id": "AUTO-CONTINUE",
        }
    )

    state = manager.session.snapshot()
    item = store.get(run_id)
    assert state["state"] == "RUNNING"
    assert state["mode"] == "CONTINUE"
    # The frozen previous final remains the continuity anchor passed into _run;
    # START_GATE will replace it with fresh telemetry before execution.
    assert item["request"]["start_position_m"] == [0.95, 0.0, -2.0]
    assert state["current_target_position_m"] == [3.0, 0.0, -2.0]


def test_start_after_land_uses_ground_live_position_for_auto_takeoff(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import orchestration.web_experiment as web_module

    monkeypatch.setattr(web_module.threading, "Thread", _NoopThread)
    store = ExperimentStore()
    manager = Step37AExperimentManager(
        project,
        store,
        simulation=_AlwaysRunningSimulation(),
    )
    manager.session.reset([0.0, 0.0, 0.0], {"managed": True, "status": "RUNNING"})
    manager.session.mark_run_started("previous", [2.0, 0.0, -2.0])
    manager.session.mark_run_completed("previous", [1.95, 0.0, -2.0], "PASS")
    manager.session.mark_landed([2.05, 0.04, -0.02])

    run_id = manager.start(
        {
            "target_position_m": [4.0, 1.0, -2.0],
            "position_tolerance_m": 0.5,
            "start_position_tolerance_m": 0.75,
            "timeout_s": 5.0,
            "frame": "LOCAL_NED",
            "scenario_id": "LANDED-RESTART",
        }
    )

    item = store.get(run_id)
    assert item["request"]["start_position_m"] == [2.05, 0.04, -0.02]
    assert manager.session.snapshot()["mode"] == "LANDED"
