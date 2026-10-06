from pathlib import Path

from src.resources.manager import ResourceManager


def test_resource_status_includes_process_metrics(tmp_path: Path):
    manager = ResourceManager(tmp_path / "runs")
    status = manager.status()
    assert "process_rss_mb" in status
    assert "process_threads" in status
    assert "open_fd_count" in status
    assert "child_process_count" in status
    assert "mavsdk_server_process_count" in status
    if status["process_rss_mb"] is not None:
        assert status["process_rss_mb"] > 0
    if status["process_threads"] is not None:
        assert status["process_threads"] >= 1
    if status["open_fd_count"] is not None:
        assert status["open_fd_count"] >= 0
