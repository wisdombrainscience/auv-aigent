#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "v1_1_revalidation"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()

    if root.exists():
        raise SystemExit(
            f"REFUSE: revalidation root already exists: {root}\n"
            "Do not overwrite experiment evidence."
        )

    hardening = project / "reports/hardening_v1_1/static_hardening.json"
    source = project / "src/capabilities/navigation/go_to_target.py"
    matrix_path = project / "experiments/NAV-001-step34-matrix.json"
    fixture = project / "tools/sitl_prepare_airborne.py"
    runner = project / "tools/run_manual_goto_baseline.py"
    auditor = project / "tools/audit_nav_attempt.py"
    venv_python = project / ".venv/bin/python"

    required = [hardening, source, matrix_path, fixture, runner, auditor, venv_python]
    for p in required:
        if not p.exists():
            raise SystemExit(f"FAIL: required path missing: {p}")

    hard = load_json(hardening)
    expected_hash = hard.get("new_source_sha256")
    actual_hash = sha256(source)
    if expected_hash != actual_hash:
        raise SystemExit(
            "FAIL: working source no longer matches static hardening evidence\n"
            f"expected={expected_hash}\nactual={actual_hash}"
        )

    # Re-run deterministic tests before freezing the simulation workspace.
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(project),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    test = subprocess.run(
        [str(venv_python), "-m", "pytest", "-q"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if test.returncode != 0:
        print(test.stdout)
        raise SystemExit("FAIL: deterministic tests did not pass")

    matrix = load_json(matrix_path)
    if matrix.get("scope") != "PX4_SITL_SIMULATION_ONLY":
        raise SystemExit("FAIL: matrix is not simulation-only")

    root.mkdir(parents=True)
    ws = root / "workspace"

    def ignore(_path, names):
        ignored = set()
        for name in names:
            if name in {".git", ".venv", "runs", "__pycache__", ".pytest_cache"}:
                ignored.add(name)
            elif name.endswith(".pyc"):
                ignored.add(name)
        return ignored

    shutil.copytree(project, ws, ignore=ignore)
    frozen_source = ws / "src/capabilities/navigation/go_to_target.py"

    if sha256(frozen_source) != actual_hash:
        raise SystemExit("FAIL: frozen workspace source hash mismatch")

    shutil.copy2(matrix_path, root / "matrix.json")
    (root / "pytest_preflight.txt").write_text(test.stdout, encoding="utf-8")

    start_script = root / "start_fresh_px4_sitl.sh"
    start_script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

echo "SIMULATION ONLY — fresh PX4 SITL instance"
sudo docker run --rm -it \\
  --network host \\
  -e PX4_SIM_MODEL=sihsim_quadx \\
  px4io/px4-sitl@sha256:c0fd482ece15bc2eecf0108ae70f8b3fa026faa4c167119f3641702b6fb20d09
""",
        encoding="utf-8",
    )
    start_script.chmod(0o755)

    scripts = root / "scenario_scripts"
    scripts.mkdir()
    fixture_reports = root / "fixture_reports"
    fixture_reports.mkdir()

    for idx, scenario in enumerate(matrix["scenarios"], start=1):
        sid = scenario["id"]
        tx, ty, tz = scenario["target_position_m"]
        scenario_id = f"V11-{sid}"
        attempt = ws / "runs/NAV-001" / scenario_id / "attempt_001"
        fixture_report = fixture_reports / f"{sid}.json"

        script = scripts / f"{idx:02d}_{sid}.sh"
        content = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT={shlex.quote(str(project))}
WS={shlex.quote(str(ws))}
ROOT={shlex.quote(str(root))}
SCENARIO_ID={shlex.quote(scenario_id)}
ATTEMPT={shlex.quote(str(attempt))}

echo "SIMULATION ONLY — NAV-001 v1.1 clean-SITL revalidation"
echo "Scenario: {sid}"
echo "Target: ({tx}, {ty}, {tz})"
echo
echo "REQUIREMENT: use a FRESH PX4 SITL process for this scenario."
echo

cd "$PROJECT"

"$PROJECT/.venv/bin/python" \
  "$PROJECT/tools/sitl_prepare_airborne.py" \
  --simulation-only \
  --report {shlex.quote(str(fixture_report))}

cd "$WS"
export PYTHONPATH="$WS"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

"$PROJECT/.venv/bin/python" \
  "$WS/tools/run_manual_goto_baseline.py" \
  --target-x {tx} \
  --target-y {ty} \
  --target-z {tz} \
  --tolerance {matrix["position_tolerance_m"]} \
  --timeout {matrix["timeout_s"]} \
  --scenario-id "$SCENARIO_ID"

"$PROJECT/.venv/bin/python" \
  "$PROJECT/tools/audit_nav_attempt.py" \
  "$ATTEMPT"

echo
echo "V11_SCENARIO={sid}"
echo "ATTEMPT=$ATTEMPT"
echo "SCENARIO_RESULT=PASS"
"""
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)

    metadata = {
        "task": "NAV-001",
        "version": "v1.1",
        "scope": "PX4_SITL_SIMULATION_ONLY",
        "working_source_sha256": actual_hash,
        "historical_v1_0_accepted_sha256": hard.get("historical_accepted_ai_sha256"),
        "scenario_count": len(matrix["scenarios"]),
        "fresh_sitl_required_per_scenario": True,
        "deterministic_preflight": "PASS",
        "workspace": str(ws),
        "status": "PREPARED",
    }
    (root / "revalidation_manifest.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=== NAV-001 V1.1 REVALIDATION PREPARATION ===")
    print(f"SOURCE_SHA256={actual_hash}")
    print("DETERMINISTIC_TESTS=PASS")
    print(f"SCENARIOS={len(matrix['scenarios'])}")
    print("FRESH_SITL_PER_SCENARIO=REQUIRED")
    print(f"ROOT={root}")
    print(f"START_SITL={start_script}")
    print(f"SCENARIO_SCRIPTS={scripts}")
    print("PREPARE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
