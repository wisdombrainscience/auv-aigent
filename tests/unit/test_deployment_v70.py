import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from orchestration.web_experiment import ExperimentStore, Step37AExperimentManager
from orchestration.web_mission import MissionWebManager
from src.resources.manager import ResourceManager, ResourcePolicy, load_resource_policy
from tools.storage_maintenance import cleanup_candidates


PROJECT = Path(__file__).resolve().parents[2]


def test_resource_policy_uses_bounded_deployment_defaults() -> None:
    policy = load_resource_policy(PROJECT / "config" / "resource_policy.yaml")
    assert policy.max_in_memory_runs == 64
    assert policy.live_buffer_points == 300
    assert policy.telemetry_batch_limit == 120
    assert policy.single_flight_poll_ms >= 500
    assert policy.json_gzip_min_bytes >= 1024


def test_resource_size_scan_is_cached(tmp_path: Path, monkeypatch) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "a.bin").write_bytes(b"a" * 1024)
    manager = ResourceManager(
        runs,
        ResourcePolicy(
            max_runs_size_mb=10,
            min_free_disk_mb=1,
            storage_status_cache_s=60,
        ),
    )
    calls = 0
    original = manager._tree_size

    def counted(path: Path) -> int:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(manager, "_tree_size", counted)
    manager.status()
    manager.status()
    assert calls == 1
    manager.invalidate_storage_cache()
    manager.status()
    assert calls == 2


def _single_manager_for_buffer() -> Step37AExperimentManager:
    manager = object.__new__(Step37AExperimentManager)
    manager.resource_policy = ResourcePolicy(
        max_in_memory_runs=2,
        live_buffer_points=4,
        telemetry_batch_limit=2,
    )
    manager._telemetry_lock = threading.Lock()
    manager._live_telemetry = OrderedDict()
    manager.store = ExperimentStore(max_items=2)
    return manager


def test_single_flight_live_telemetry_is_delta_and_bounded() -> None:
    manager = _single_manager_for_buffer()
    for i in range(6):
        manager._push_live_telemetry("run-a", {"timestamp": float(i), "x": i})
    assert [r["x"] for r in manager.latest_telemetry_for_run("run-a")] == [4, 5]
    assert [r["x"] for r in manager.latest_telemetry_for_run("run-a", since_timestamp=4.0)] == [5]
    manager._push_live_telemetry("run-b", {"timestamp": 1.0, "x": 1})
    manager._push_live_telemetry("run-c", {"timestamp": 1.0, "x": 1})
    assert "run-a" not in manager._live_telemetry


def test_mission_status_can_exclude_trajectory_without_losing_live_buffer() -> None:
    manager = object.__new__(MissionWebManager)
    manager.resource_policy = ResourcePolicy(
        max_in_memory_runs=2,
        live_buffer_points=4,
        telemetry_batch_limit=2,
    )
    manager.store = ExperimentStore(max_items=2)
    manager._trajectory_lock = threading.Lock()
    manager._live_trajectory = OrderedDict()
    manager.store.create("m1", {"status": "RUNNING", "trajectory": []})
    manager._create_trajectory_buffer("m1")
    with manager._trajectory_lock:
        manager._live_trajectory["m1"].extend(
            [
                {"timestamp": 1.0, "x": 1},
                {"timestamp": 2.0, "x": 2},
                {"timestamp": 3.0, "x": 3},
            ]
        )
    compact = manager.get("m1", include_trajectory=False)
    assert compact is not None and "trajectory" not in compact
    assert [r["x"] for r in manager.latest_trajectory_for_run("m1", since_timestamp=1.0)] == [2, 3]


def test_retention_cleanup_is_safe_and_keeps_recent_attempts(tmp_path: Path) -> None:
    project = tmp_path
    (project / "config").mkdir()
    (project / "config" / "resource_policy.yaml").write_text(
        """
version: 1
retention:
  pass_days: 30
  fail_days: 90
  incomplete_days: 7
  keep_latest_per_scenario: 1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    scenario = project / "runs" / "NAV-001" / "S"
    scenario.mkdir(parents=True)
    old = scenario / "attempt_001"
    recent = scenario / "attempt_002"
    for attempt in (old, recent):
        attempt.mkdir()
        (attempt / "verification.json").write_text(
            json.dumps({"overall": "PASS"}), encoding="utf-8"
        )
        (attempt / "finalized.json").write_text("{}", encoding="utf-8")
    now = time.time()
    old_time = now - 60 * 86400
    for child in old.iterdir():
        child.touch()
        import os
        os.utime(child, (old_time, old_time))
    import os
    os.utime(old, (old_time, old_time))
    candidates = cleanup_candidates(project, now=now)
    assert [x.path.name for x in candidates] == ["attempt_001"]


def test_unified_deployment_cli_and_legacy_entrypoints_exist() -> None:
    for name in (
        "uavctl",
        "install-uav",
        "setup-uav",
        "start-uav",
        "health-uav",
        "test-uav",
        "storage-uav",
        "capability-uav",
        "mission-uav",
        "soak-uav",
    ):
        path = PROJECT / name
        assert path.is_file()
        assert path.stat().st_mode & 0o111


def test_runtime_data_dir_can_be_externalized(tmp_path: Path, monkeypatch) -> None:
    from src.runtime_paths import runs_root

    project = tmp_path / "source"
    project.mkdir()
    data = tmp_path / "persistent"
    monkeypatch.setenv("UAV_DATA_DIR", str(data))
    assert runs_root(project) == data.resolve() / "runs"


def test_release_builder_excludes_runtime_and_local_env(tmp_path: Path) -> None:
    import zipfile
    from tools.build_release import build

    fake = tmp_path / "project"
    fake.mkdir()
    (fake / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="1.2.3"\n', encoding="utf-8"
    )
    (fake / "keep.txt").write_text("ok", encoding="utf-8")
    (fake / "uav.env").write_text("SECRET=local", encoding="utf-8")
    (fake / "runs" / "A").mkdir(parents=True)
    (fake / "runs" / "A" / "evidence.bin").write_bytes(b"x")
    out = tmp_path / "dist"
    archive, checksum = build(fake, out)
    assert archive.is_file() and checksum.is_file()
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    assert any(name.endswith("/keep.txt") for name in names)
    assert not any("/runs/" in name for name in names)
    assert not any(name.endswith("/uav.env") for name in names)


def test_web_console_uses_incremental_telemetry_and_gzip() -> None:
    server = (PROJECT / "tools" / "step37a_console.py").read_text(encoding="utf-8")
    single = (PROJECT / "user_interface" / "step37a_console.html").read_text(encoding="utf-8")
    mission = (PROJECT / "user_interface" / "mission_builder.html").read_text(encoding="utf-8")
    assert "Content-Encoding', 'gzip'" in server
    assert "since_timestamp" in server
    assert "status?compact=1" in mission
    assert "lastTelemetryTs" in single
    assert "lastMissionTelemetryTs" in mission


def test_github_preflight_passes_for_source_tree() -> None:
    from tools.github_preflight import scan

    issues, count = scan(PROJECT)
    assert count > 0
    assert issues == []


def test_release_builder_manifest_matches_archive_and_excludes_source_manifest(tmp_path: Path) -> None:
    import hashlib
    import zipfile
    from tools.build_release import build

    fake = tmp_path / "project"
    fake.mkdir()
    (fake / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="1.2.3"\n', encoding="utf-8"
    )
    (fake / "keep.txt").write_text("ok", encoding="utf-8")
    (fake / "RELEASE_MANIFEST.json").write_text("stale", encoding="utf-8")
    archive, _ = build(fake, tmp_path / "dist")
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        root = names[0].split("/", 1)[0]
        manifest = json.loads(z.read(f"{root}/RELEASE_MANIFEST.json"))
        listed = {x["path"]: x for x in manifest["files"]}
        assert "RELEASE_MANIFEST.json" not in listed
        payload = z.read(f"{root}/keep.txt")
        assert hashlib.sha256(payload).hexdigest() == listed["keep.txt"]["sha256"]


def test_test_runner_isolates_runtime_configuration() -> None:
    script = (PROJECT / "test-uav").read_text(encoding="utf-8")
    for name in (
        "UAV_DATA_DIR",
        "UAV_SIM_BACKEND",
        "UAV_SIM_IMAGE",
        "UAV_SIM_MODEL",
        "UAV_GAZEBO_GUI",
    ):
        assert f"-u {name}" in script
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in script
    assert "PYTHONNOUSERSITE=1" in script


def test_installer_isolates_pip_from_host_ros_pythonpath() -> None:
    script = (PROJECT / "uavctl").read_text(encoding="utf-8")
    assert 'env -u PYTHONPATH PYTHONNOUSERSITE=1' in script
    assert '"$VENV_PY" -m pip check' in script
