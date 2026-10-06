#!/usr/bin/env python3
"""
Prepare NAV-001 STEP 34 experiment-matrix workspace.

Simulation-only. Does not start PX4 and does not control a real UAV.
It verifies the accepted candidate, creates an isolated workspace,
overlays the accepted candidate, and writes one run script per scenario.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_MATRIX_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step34_matrix"


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
    ap.add_argument("--matrix-root", type=Path, default=DEFAULT_MATRIX_ROOT)
    ap.add_argument(
        "--matrix",
        type=Path,
        default=Path("experiments/NAV-001-step34-matrix.json"),
    )
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    matrix_root = args.matrix_root.expanduser().resolve()
    matrix_path = args.matrix
    if not matrix_path.is_absolute():
        matrix_path = (project / matrix_path).resolve()

    status_path = project / "reports/pipeline_v2/step19_30_status.json"
    if not status_path.is_file():
        raise SystemExit(f"FAIL: accepted status missing: {status_path}")

    status = load_json(status_path)
    if status.get("candidate_status") != "ACCEPTED":
        raise SystemExit("FAIL: candidate_status is not ACCEPTED")
    if status.get("scope") != "SIMULATION_ONLY":
        raise SystemExit("FAIL: accepted scope is not SIMULATION_ONLY")

    candidate_hash = status["candidate_sha256"]
    accepted_dir = project / "reports/pipeline_v2/accepted/NAV-001" / candidate_hash
    accepted_candidate = accepted_dir / "go_to_target.py"
    manifest_path = accepted_dir / "acceptance_manifest.json"

    if not accepted_candidate.is_file() or not manifest_path.is_file():
        raise SystemExit("FAIL: accepted candidate package incomplete")
    if sha256(accepted_candidate) != candidate_hash:
        raise SystemExit("FAIL: accepted candidate hash mismatch")

    matrix_doc = load_json(matrix_path)
    if matrix_doc.get("scope") != "PX4_SITL_SIMULATION_ONLY":
        raise SystemExit("FAIL: matrix scope is not PX4_SITL_SIMULATION_ONLY")

    scenarios = matrix_doc.get("scenarios", [])
    if len(scenarios) < 3:
        raise SystemExit("FAIL: experiment matrix needs at least 3 scenarios")

    ws = matrix_root / "workspace"
    if ws.exists():
        shutil.rmtree(ws)

    def ignore(_path, names):
        ignored = set()
        for name in names:
            if name in {".git", ".venv", "runs", "__pycache__"}:
                ignored.add(name)
            elif name.endswith(".pyc"):
                ignored.add(name)
        return ignored

    shutil.copytree(project, ws, ignore=ignore)

    target = ws / "src/capabilities/navigation/go_to_target.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(accepted_candidate, target)

    if sha256(target) != candidate_hash:
        raise SystemExit("FAIL: workspace candidate hash mismatch")

    matrix_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(matrix_path, matrix_root / "matrix.json")

    scripts_dir = matrix_root / "scenario_scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    venv_python = project / ".venv/bin/python"
    fixture = project / "tools/sitl_prepare_airborne.py"
    runner = ws / "tools/run_manual_goto_baseline.py"

    if not venv_python.is_file():
        raise SystemExit("FAIL: project venv Python missing")
    if not fixture.is_file():
        raise SystemExit("FAIL: clean SITL fixture missing")
    if not runner.is_file():
        raise SystemExit("FAIL: isolated runner missing")

    for index, scenario in enumerate(scenarios, start=1):
        sid = scenario["id"]
        tx, ty, tz = scenario["target_position_m"]
        scenario_id = f"STEP34-{sid}"

        script = scripts_dir / f"{index:02d}_{sid}.sh"
        fixture_report = matrix_root / "fixture_reports" / f"{sid}.json"

        content = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT={shlex.quote(str(project))}
WS={shlex.quote(str(ws))}
MATRIX_ROOT={shlex.quote(str(matrix_root))}
SCENARIO_ID={shlex.quote(scenario_id)}

echo "SIMULATION ONLY — NAV-001 STEP 34"
echo "Scenario: {sid}"
echo "Target: ({tx}, {ty}, {tz})"
echo
echo "PRECONDITION: start a FRESH PX4 SITL instance before running this script."
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
  --tolerance {matrix_doc["position_tolerance_m"]} \
  --timeout {matrix_doc["timeout_s"]} \
  --scenario-id "$SCENARIO_ID"

ATTEMPT="$WS/runs/NAV-001/$SCENARIO_ID/attempt_001"

"$PROJECT/.venv/bin/python" \
  "$PROJECT/tools/audit_nav_attempt.py" \
  "$ATTEMPT"

echo
echo "SCENARIO_DONE={sid}"
echo "ATTEMPT=$ATTEMPT"
"""
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)

    metadata = {
        "task": "NAV-001",
        "step": 34,
        "scope": "SIMULATION_ONLY",
        "candidate_sha256": candidate_hash,
        "workspace": str(ws),
        "matrix": str(matrix_root / "matrix.json"),
        "scenario_count": len(scenarios),
        "status": "PREPARED",
        "fresh_sitl_required_per_scenario": True,
    }
    (matrix_root / "step34_preparation.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=== NAV-001 STEP 34 PREPARATION ===")
    print("ACCEPTED_CANDIDATE_HASH=PASS")
    print("ISOLATED_WORKSPACE=PASS")
    print(f"SCENARIOS={len(scenarios)}")
    print("FRESH_SITL_PER_SCENARIO=REQUIRED")
    print(f"MATRIX_ROOT={matrix_root}")
    print(f"WORKSPACE={ws}")
    print(f"SCRIPTS={scripts_dir}")
    print("STEP34_PREPARATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
