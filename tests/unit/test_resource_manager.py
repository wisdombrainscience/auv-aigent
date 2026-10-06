from pathlib import Path

import pytest

from src.resources.manager import ResourceLimitError, ResourceManager, ResourcePolicy


def test_resource_manager_reports_bounded_policy(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "sample.bin").write_bytes(b"x" * 1024)
    manager = ResourceManager(
        runs,
        ResourcePolicy(max_runs_size_mb=10, min_free_disk_mb=1, max_timeout_s=60),
    )
    status = manager.status()
    assert status["ok"] is True
    assert status["runs_bytes"] >= 1024
    assert status["max_timeout_s"] == 60


def test_resource_manager_blocks_excessive_timeout(tmp_path: Path):
    manager = ResourceManager(
        tmp_path / "runs",
        ResourcePolicy(max_timeout_s=10, min_free_disk_mb=1, max_runs_size_mb=10),
    )
    with pytest.raises(ResourceLimitError):
        manager.assert_can_start(11)
