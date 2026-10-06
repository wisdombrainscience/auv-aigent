from pathlib import Path

import pytest

from orchestration.step37a_simulation import SimulationConfig, SimulationSupervisor


GAZEBO_IMAGE = "px4io/px4-sitl-gazebo:v1.18.0-beta1-660-g61907962c9"
SIH_IMAGE = (
    "px4io/px4-sitl@sha256:"
    "c0fd482ece15bc2eecf0108ae70f8b3fa026faa4c167119f3641702b6fb20d09"
)


def test_default_backend_uses_matching_gazebo_runtime(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("UAV_SIM_BACKEND", raising=False)
    monkeypatch.delenv("UAV_SIM_IMAGE", raising=False)
    monkeypatch.delenv("UAV_SIM_MODEL", raising=False)
    sup = SimulationSupervisor(tmp_path)
    assert sup.config.backend == "gazebo"
    assert sup.config.image == GAZEBO_IMAGE
    assert sup.config.model == "gz_x500"


def test_gazebo_headless_command_sets_headless_when_display_unavailable(tmp_path: Path):
    sup = SimulationSupervisor(
        tmp_path,
        SimulationConfig(
            backend="gazebo",
            image=GAZEBO_IMAGE,
            model="gz_x500",
            gazebo_gui="off",
        ),
    )
    gui_args, gui = sup._prepare_x11()
    cmd = sup._container_run_command("0,0,0,0,0,0", gui_args, gui)
    assert GAZEBO_IMAGE in cmd
    assert "PX4_SIM_MODEL=gz_x500" in cmd
    assert "PX4_GZ_WORLD=default" in cmd
    assert "HEADLESS=1" in cmd


def test_sih_backend_keeps_lightweight_image_and_does_not_add_gazebo_env(tmp_path: Path):
    sup = SimulationSupervisor(
        tmp_path,
        SimulationConfig(
            backend="sih",
            image=SIH_IMAGE,
            model="sihsim_quadx",
            gazebo_gui="off",
        ),
    )
    gui_args, gui = sup._prepare_x11()
    cmd = sup._container_run_command("0,0,0,0,0,0", gui_args, gui)
    assert SIH_IMAGE in cmd
    assert "PX4_SIM_MODEL=sihsim_quadx" in cmd
    assert not any(str(item).startswith("PX4_GZ_") for item in cmd)
    assert "HEADLESS=1" not in cmd


def test_rejects_gazebo_model_with_official_sih_image(tmp_path: Path):
    with pytest.raises(ValueError, match="lightweight px4io/px4-sitl SIH image"):
        SimulationSupervisor(
            tmp_path,
            SimulationConfig(
                backend="gazebo",
                image=SIH_IMAGE,
                model="gz_x500",
            ),
        )


def test_start_new_uses_backend_aware_command_without_undefined_gui_state(tmp_path: Path, monkeypatch):
    sup = SimulationSupervisor(
        tmp_path,
        SimulationConfig(
            backend="sih",
            image=SIH_IMAGE,
            model="sihsim_quadx",
            gazebo_gui="off",
        ),
    )

    class FakeProcess:
        returncode = None

        def poll(self):
            return None

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProcess()

    tick = {"value": 0.0}

    def fake_monotonic():
        tick["value"] += 1.1
        return tick["value"]

    monkeypatch.setattr(sup, "stop", lambda: None)
    monkeypatch.setattr(sup, "is_running", lambda: True)
    monkeypatch.setattr(sup, "gazebo_gui_status", lambda: {"status": "DISABLED"})
    monkeypatch.setattr("orchestration.step37a_simulation.subprocess.Popen", fake_popen)
    monkeypatch.setattr("orchestration.step37a_simulation.time.monotonic", fake_monotonic)
    monkeypatch.setattr("orchestration.step37a_simulation.time.sleep", lambda _seconds: None)

    result = sup.start_new([0.0, 0.0, 0.0])
    assert result["status"] == "RUNNING"
    assert result["backend"] == "sih"
    assert "PX4_SIM_MODEL=sihsim_quadx" in captured["cmd"]


def test_gazebo_gui_on_fails_before_container_start_when_display_unavailable(tmp_path: Path, monkeypatch):
    sup = SimulationSupervisor(
        tmp_path,
        SimulationConfig(
            backend="gazebo",
            image=GAZEBO_IMAGE,
            model="gz_x500",
            gazebo_gui="on",
        ),
    )
    monkeypatch.setattr(sup, "stop", lambda: None)
    monkeypatch.setattr(
        sup,
        "_prepare_x11",
        lambda: ([], {"available": False, "error": "DISPLAY unavailable"}),
    )

    with pytest.raises(RuntimeError, match="DISPLAY unavailable"):
        sup.start_new([0.0, 0.0, 0.0])
