from pathlib import Path

from orchestration.step37a_simulation import SimulationConfig, SimulationSupervisor


def test_docker_host_log_mode_does_not_open_unbounded_runtime_log(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UAV_SIM_HOST_LOG_MODE", "docker")
    sup = SimulationSupervisor(
        tmp_path,
        SimulationConfig(
            backend="sih",
            image="px4io/px4-sitl@sha256:c0fd482ece15bc2eecf0108ae70f8b3fa026faa4c167119f3641702b6fb20d09",
            model="sihsim_quadx",
            gazebo_gui="off",
        ),
    )
    monkeypatch.setattr(sup, "is_running", lambda: False)
    monkeypatch.setattr(sup, "gazebo_gui_status", lambda: {"status": "DISABLED"})
    assert sup._host_log_mode == "docker"
    assert sup.live_status()["host_log_mode"] == "docker"
