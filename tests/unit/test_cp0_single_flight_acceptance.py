from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "cp0_single_flight_acceptance.py"
spec = importlib.util.spec_from_file_location("cp0_single_flight_acceptance", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_configured_uav_port_defaults_to_8370(tmp_path: Path) -> None:
    assert mod.configured_uav_port(tmp_path) == 8370


def test_configured_uav_port_reads_quoted_uav_env(tmp_path: Path) -> None:
    (tmp_path / "uav.env").write_text('UAV_PORT="9123"\n', encoding="utf-8")
    assert mod.configured_uav_port(tmp_path) == 9123


def test_configured_uav_port_reads_export_form(tmp_path: Path) -> None:
    (tmp_path / "uav.env").write_text("export UAV_PORT='8456'\n", encoding="utf-8")
    assert mod.configured_uav_port(tmp_path) == 8456
