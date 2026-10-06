from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "cp0_freeze.py"
spec = importlib.util.spec_from_file_location("cp0_freeze", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_project_version_parser(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion = "1.2.3"\n', encoding="utf-8")
    assert mod.read_project_version(tmp_path) == "1.2.3"


def test_exclusion_rules() -> None:
    from pathlib import PurePosixPath
    assert mod.is_excluded(PurePosixPath("runs/A/file.json"))
    assert mod.is_excluded(PurePosixPath("src/__pycache__/a.pyc"))
    assert not mod.is_excluded(PurePosixPath("src/core/a.py"))


def test_default_single_flight_uses_ground_bootstrap_helper(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / "tools/cp0_single_flight_acceptance.py").write_text("# helper\n", encoding="utf-8")
    (tmp_path / ".venv/bin/python").write_text("", encoding="utf-8")
    assert mod.default_single_flight_command(tmp_path) == ".venv/bin/python tools/cp0_single_flight_acceptance.py --project ."
