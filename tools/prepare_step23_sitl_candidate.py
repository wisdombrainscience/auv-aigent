#!/usr/bin/env python3
"""
Prepare STEP 23: run the validated Recovery-003 candidate in PX4 SITL only.

This tool:
1) verifies STEP 19 repeatability evidence from MANUAL-BASELINE attempt_002/003;
2) verifies the Recovery-003 full-test report says PASS;
3) verifies protected hashes from NAV-001 implementation contract;
4) creates an isolated SITL workspace and overlays the AI candidate;
5) freezes the manual-baseline input as the candidate SITL testcase;
6) writes two shell scripts:
   - start_px4_sitl_simulation.sh
   - run_candidate_sitl.sh

It DOES NOT execute the simulator or control real hardware.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path


DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_RUN2 = (
    Path.home()
    / "UAV"
    / "st0_agent_jobs"
    / "NAV-001"
    / "recovery_003"
    / "run_002_method_scope"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verdict(doc) -> str | None:
    """Conservative extraction. UNKNOWN if no explicit PASS/FAIL can be found."""
    if not isinstance(doc, dict):
        return None

    for key in ("overall", "overall_result", "verdict", "result", "status"):
        value = doc.get(key)
        if isinstance(value, str) and value.upper() in {"PASS", "FAIL"}:
            return value.upper()

    checks = doc.get("checks")
    if isinstance(checks, dict) and checks:
        vals = []
        for value in checks.values():
            if isinstance(value, str) and value.upper() in {"PASS", "FAIL"}:
                vals.append(value.upper())
            elif isinstance(value, dict):
                nested = None
                for key in ("status", "result", "verdict"):
                    v = value.get(key)
                    if isinstance(v, str) and v.upper() in {"PASS", "FAIL"}:
                        nested = v.upper()
                        break
                if nested:
                    vals.append(nested)
        if vals and len(vals) == len(checks):
            return "PASS" if all(v == "PASS" for v in vals) else "FAIL"

    return None


def telemetry_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", newline="") as f:
        return max(sum(1 for _ in csv.reader(f)) - 1, 0)


def fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--run2", type=Path, default=DEFAULT_RUN2)
    ap.add_argument("--scenario-id", default="AI-RECOVERY-003-SITL")
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    run2 = args.run2.expanduser().resolve()
    candidate = run2 / "candidate" / "go_to_target.py"
    full_report = run2 / "candidate_validation_full.json"
    contract_path = project / "specs" / "NAV-001-IMPLEMENTATION-CONTRACT.yaml"

    print("=== STEP 23 PREPARATION ===")

    if not project.is_dir():
        fail(f"project missing: {project}")
    if not candidate.is_file():
        fail(f"candidate missing: {candidate}")
    if not contract_path.is_file():
        fail(f"contract missing: {contract_path}")
    if not full_report.is_file():
        fail(
            "full-test report missing; run validate_candidate.py --full-tests first: "
            f"{full_report}"
        )

    full = load_json(full_report)
    if full.get("overall") != "PASS":
        fail(f"full-test report is not PASS: {full_report}")
    print("RECOVERY_003_FULL_TESTS=PASS")

    contract = load_json(contract_path)
    protected_bad = []
    for rel, expected in contract["protected_hashes"].items():
        p = project / rel
        actual = sha256(p) if p.is_file() else None
        if actual != expected:
            protected_bad.append((rel, expected, actual))
    if protected_bad:
        fail(f"protected hash mismatch: {protected_bad}")
    print("PROTECTED_HASHES=PASS")

    base = project / "runs" / "NAV-001" / "MANUAL-BASELINE"
    a2 = base / "attempt_002"
    a3 = base / "attempt_003"

    required = ("input.json", "telemetry.csv", "module_output.json", "verification.json")
    for name, attempt in (("attempt_002", a2), ("attempt_003", a3)):
        for rel in required:
            p = attempt / rel
            if not p.is_file() or p.stat().st_size == 0:
                fail(f"{name}/{rel} missing or empty")
        rows = telemetry_rows(attempt / "telemetry.csv")
        if rows < 2:
            fail(f"{name}/telemetry.csv has too few data rows: {rows}")

    i2 = load_json(a2 / "input.json")
    i3 = load_json(a3 / "input.json")
    compare_keys = (
        "frame",
        "target_position_m",
        "position_tolerance_m",
        "timeout_s",
        "scenario_id",
    )
    mismatches = {
        k: {"attempt_002": i2.get(k), "attempt_003": i3.get(k)}
        for k in compare_keys
        if i2.get(k) != i3.get(k)
    }
    if mismatches:
        fail(f"STEP19 repeatability input mismatch: {mismatches}")

    v2 = verdict(load_json(a2 / "verification.json"))
    v3 = verdict(load_json(a3 / "verification.json"))
    if v2 != "PASS" or v3 != "PASS":
        fail(f"STEP19 verifier gate not PASS: attempt_002={v2}, attempt_003={v3}")

    print("STEP19_REPEATABILITY=PASS")
    print("MANUAL_ATTEMPT_002_VERIFIER=PASS")
    print("MANUAL_ATTEMPT_003_VERIFIER=PASS")

    venv_python = project / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        fail(f"project venv Python missing: {venv_python}")

    mavsdk_check = subprocess.run(
        [str(venv_python), "-c", "import mavsdk; print('MAVSDK_OK')"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if mavsdk_check.returncode != 0:
        fail(
            "mavsdk is unavailable in project venv; do not use system Python.\n"
            + mavsdk_check.stdout
        )
    print("MAVSDK_VENV=PASS")

    ws = run2 / "sitl_workspace"
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
    target = ws / "src" / "capabilities" / "navigation" / "go_to_target.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, target)

    frozen_input = run2 / "frozen_sitl_input.json"
    sitl_input = dict(i2)
    sitl_input["scenario_id"] = args.scenario_id
    frozen_input.write_text(
        json.dumps(sitl_input, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    target_pos = sitl_input["target_position_m"]
    tol = sitl_input["position_tolerance_m"]
    timeout = sitl_input["timeout_s"]

    metadata = {
        "mode": "SIMULATION_ONLY",
        "step": 23,
        "candidate": str(candidate),
        "candidate_sha256": sha256(candidate),
        "workspace_candidate_sha256": sha256(target),
        "full_test_report": str(full_report),
        "full_tests": "PASS",
        "step19_repeatability": "PASS",
        "frozen_input": str(frozen_input),
        "scenario_id": args.scenario_id,
        "accepted": False,
        "acceptance_requires": "independent deterministic verifier PASS",
    }
    (run2 / "sitl_preparation.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    start_script = run2 / "start_px4_sitl_simulation.sh"
    start_script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

echo "SIMULATION ONLY: PX4 SITL / SIH"
sudo docker run --rm -it \\
  --network host \\
  -e PX4_SIM_MODEL=sihsim_quadx \\
  px4io/px4-sitl@sha256:c0fd482ece15bc2eecf0108ae70f8b3fa026faa4c167119f3641702b6fb20d09
""",
        encoding="utf-8",
    )
    start_script.chmod(0o755)

    runner = ws / "tools" / "run_manual_goto_baseline.py"
    if not runner.is_file():
        fail(f"runner missing from isolated workspace: {runner}")

    command = [
        str(venv_python),
        str(runner),
        "--target-x", str(target_pos[0]),
        "--target-y", str(target_pos[1]),
        "--target-z", str(target_pos[2]),
        "--tolerance", str(tol),
        "--timeout", str(timeout),
        "--scenario-id", args.scenario_id,
    ]
    quoted = " ".join(shlex.quote(x) for x in command)

    run_script = run2 / "run_candidate_sitl.sh"
    run_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f"WS={shlex.quote(str(ws))}\n"
        'cd "$WS"\n'
        'export PYTHONPATH="$WS"\n'
        'export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1\n\n'
        'echo "SIMULATION ONLY: running Recovery-003 candidate"\n'
        + quoted
        + "\n",
        encoding="utf-8",
    )
    run_script.chmod(0o755)

    print(f"CANDIDATE_SHA256={metadata['candidate_sha256']}")
    print(f"SITL_WORKSPACE={ws}")
    print(f"FROZEN_INPUT={frozen_input}")
    print(f"START_SCRIPT={start_script}")
    print(f"RUN_SCRIPT={run_script}")
    print("STEP23_PREPARATION=PASS")
    print("ACCEPTED=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
