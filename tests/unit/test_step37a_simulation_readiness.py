from pathlib import Path
from types import SimpleNamespace

from orchestration.step37a_simulation import SimulationSupervisor


def test_live_status_reports_stopped_when_container_is_absent(tmp_path: Path, monkeypatch):
    sup = SimulationSupervisor(tmp_path)
    monkeypatch.setattr(
        "orchestration.step37a_simulation.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert sup.is_running() is False
    assert sup.live_status()["status"] == "STOPPED"


def test_live_status_reports_running_from_docker_inspect(tmp_path: Path, monkeypatch):
    sup = SimulationSupervisor(tmp_path)
    monkeypatch.setattr(
        "orchestration.step37a_simulation.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="true\n"),
    )
    assert sup.is_running() is True
    assert sup.live_status()["status"] == "RUNNING"


def test_gazebo_gui_auto_is_nonfatal_without_display(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    sup = SimulationSupervisor(tmp_path)
    args, status = sup._prepare_x11()
    assert args == []
    assert status["available"] is False
    assert "DISPLAY" in status["error"]
