#!/usr/bin/env python3
"""
Close NAV-001 Pipeline V2 acceptance gates after clean SITL verification.

Simulation-only evidence workflow. This tool does NOT control a vehicle and
does NOT overwrite the project source candidate. It validates existing files,
then creates an immutable acceptance package under reports/pipeline_v2/accepted.

Required gates:
- Recovery-003 full test report PASS
- protected hashes unchanged
- manual clean baseline attempt_004 PASS
- manual clean baseline attempt_005 PASS
- attempt_004 and attempt_005 use the same testcase
- AI candidate SITL attempt_002 has clean start and verifier PASS

If any gate is missing, the script exits without accepting the candidate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
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


def require_file(path: Path, label: str):
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"GATE FAIL: {label} missing/empty: {path}")


def attempt_audit(attempt: Path) -> dict:
    for name in ("input.json", "telemetry.csv", "module_output.json", "verification.json"):
        require_file(attempt / name, f"{attempt.name}/{name}")

    inp = load_json(attempt / "input.json")
    mod = load_json(attempt / "module_output.json")
    ver = load_json(attempt / "verification.json")

    with (attempt / "telemetry.csv").open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) < 2:
        raise SystemExit(f"GATE FAIL: too few telemetry rows in {attempt}")

    first = rows[0]
    last = rows[-1]
    start = tuple(float(first[k]) for k in ("x", "y", "z"))
    end = tuple(float(last[k]) for k in ("x", "y", "z"))
    target = tuple(float(x) for x in inp["target_position_m"])

    start_xy = math.hypot(start[0], start[1])
    clean = (
        start_xy <= 2.0
        and abs(start[2] - (-2.0)) <= 1.0
        and str(first.get("vehicle_state", "")).upper() == "AIRBORNE"
        and str(first.get("localization_valid", "")).lower() == "true"
    )

    return {
        "path": str(attempt),
        "input": inp,
        "module_status": mod.get("status"),
        "verifier_overall": ver.get("overall"),
        "position_error_m": ver.get("position_error_m"),
        "elapsed_time_s": ver.get("elapsed_time_s"),
        "start_position": start,
        "end_position": end,
        "initial_distance_to_target_m": math.dist(start, target),
        "final_distance_to_target_m": math.dist(end, target),
        "clean_start": clean,
        "telemetry_rows": len(rows),
        "hashes": {
            "input.json": sha256(attempt / "input.json"),
            "telemetry.csv": sha256(attempt / "telemetry.csv"),
            "module_output.json": sha256(attempt / "module_output.json"),
            "verification.json": sha256(attempt / "verification.json"),
        },
    }


def same_testcase(a: dict, b: dict) -> bool:
    keys = ("frame", "target_position_m", "position_tolerance_m", "timeout_s", "scenario_id")
    return all(a.get(k) == b.get(k) for k in keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--run2", type=Path, default=DEFAULT_RUN2)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    run2 = args.run2.expanduser().resolve()

    contract_path = project / "specs/NAV-001-IMPLEMENTATION-CONTRACT.yaml"
    full_report_path = run2 / "candidate_validation_full.json"
    candidate = run2 / "candidate/go_to_target.py"

    for p, label in (
        (contract_path, "implementation contract"),
        (full_report_path, "full test report"),
        (candidate, "Recovery-003 candidate"),
    ):
        require_file(p, label)

    contract = load_json(contract_path)
    full_report = load_json(full_report_path)

    gates = {}

    gates["full_tests"] = full_report.get("overall") == "PASS"
    if not gates["full_tests"]:
        raise SystemExit("GATE FAIL: Recovery-003 full tests are not PASS")

    protected = {}
    protected_ok = True
    for rel, expected in contract["protected_hashes"].items():
        p = project / rel
        actual = sha256(p) if p.is_file() else None
        protected[rel] = {"expected": expected, "actual": actual}
        protected_ok = protected_ok and (actual == expected)
    gates["protected_hashes"] = protected_ok
    if not protected_ok:
        raise SystemExit("GATE FAIL: protected file hash mismatch")

    manual_root = project / "runs/NAV-001/MANUAL-BASELINE"
    m4 = attempt_audit(manual_root / "attempt_004")
    m5 = attempt_audit(manual_root / "attempt_005")

    for label, audit in (("attempt_004", m4), ("attempt_005", m5)):
        if not audit["clean_start"]:
            raise SystemExit(f"GATE FAIL: {label} clean-start is not PASS")
        if audit["module_status"] != "completed":
            raise SystemExit(f"GATE FAIL: {label} module status != completed")
        if audit["verifier_overall"] != "PASS":
            raise SystemExit(f"GATE FAIL: {label} verifier != PASS")

    gates["manual_attempt_004"] = True
    gates["manual_attempt_005"] = True
    gates["manual_repeatability_same_testcase"] = same_testcase(m4["input"], m5["input"])
    if not gates["manual_repeatability_same_testcase"]:
        raise SystemExit("GATE FAIL: attempt_004 and attempt_005 testcase mismatch")

    ai_attempt = (
        run2
        / "sitl_workspace/runs/NAV-001/AI-RECOVERY-003-SITL/attempt_002"
    )
    ai = attempt_audit(ai_attempt)

    if not ai["clean_start"]:
        raise SystemExit("GATE FAIL: AI attempt_002 clean-start != PASS")
    if ai["module_status"] != "completed":
        raise SystemExit("GATE FAIL: AI attempt_002 module status != completed")
    if ai["verifier_overall"] != "PASS":
        raise SystemExit("GATE FAIL: AI attempt_002 verifier != PASS")

    gates["ai_sitl_attempt_002_clean_start"] = True
    gates["ai_sitl_attempt_002_verifier"] = True

    candidate_hash = sha256(candidate)
    accepted_dir = project / "reports/pipeline_v2/accepted/NAV-001" / candidate_hash
    accepted_dir.mkdir(parents=True, exist_ok=True)

    accepted_candidate = accepted_dir / "go_to_target.py"
    if accepted_candidate.exists():
        if sha256(accepted_candidate) != candidate_hash:
            raise SystemExit("GATE FAIL: accepted package path exists with different content")
    else:
        shutil.copy2(candidate, accepted_candidate)

    # Copy small, essential evidence files. Keep telemetry referenced by hash/path.
    for prefix, attempt in (
        ("manual_attempt_004", manual_root / "attempt_004"),
        ("manual_attempt_005", manual_root / "attempt_005"),
        ("ai_attempt_002", ai_attempt),
    ):
        for name in ("input.json", "module_output.json", "verification.json"):
            shutil.copy2(attempt / name, accepted_dir / f"{prefix}_{name}")

    manifest = {
        "task": "NAV-001",
        "status": "ACCEPTED",
        "scope": "PX4_SITL_SIMULATION_ONLY",
        "accepted_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate": {
            "source": str(candidate),
            "sha256": candidate_hash,
            "accepted_copy": str(accepted_candidate),
        },
        "gates": gates,
        "protected_hashes": protected,
        "manual_baseline": {
            "attempt_004": m4,
            "attempt_005": m5,
            "repeatability": "PASS",
        },
        "ai_candidate_sitl": {
            "attempt_002": ai,
            "result": "PASS",
        },
        "acceptance_basis": (
            "Full tests PASS + clean repeated manual baseline PASS + "
            "clean PX4 SITL candidate run + independent deterministic verifier PASS"
        ),
        "note": (
            "Module position_error_m is diagnostic; official acceptance uses "
            "independent verification from telemetry."
        ),
    }

    (accepted_dir / "acceptance_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    status = {
        "STEP_19_manual_baseline_repeatability": "PASS",
        "STEP_20_ai_implementation": "PASS",
        "STEP_21_boundary": "PASS",
        "STEP_22_unit_and_full_tests": "PASS",
        "STEP_23_user_defined_sitl_experiment": "PASS",
        "STEP_24_collect_evidence": "PASS",
        "STEP_25_deterministic_verifier": "PASS",
        "STEP_26_failure_package": "NOT_REQUIRED_FOR_FINAL_RUN",
        "STEP_27_29_recovery_cycle": "PASS",
        "STEP_30_accept_only_after_verifier_pass": "PASS",
        "candidate_status": "ACCEPTED",
        "scope": "SIMULATION_ONLY",
        "candidate_sha256": candidate_hash,
    }
    status_path = project / "reports/pipeline_v2/step19_30_status.json"
    status_path.write_text(
        json.dumps(status, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report_md = f"""# NAV-001 GoToTarget — Pipeline V2 Acceptance Report

## Final status

**ACCEPTED — PX4 SITL simulation scope**

Candidate SHA256:

`{candidate_hash}`

## Manual clean baseline

| Attempt | Clean start | Module | Verifier | Position error |
|---|---|---|---|---:|
| attempt_004 | PASS | {m4['module_status']} | {m4['verifier_overall']} | {m4['position_error_m']:.6f} m |
| attempt_005 | PASS | {m5['module_status']} | {m5['verifier_overall']} | {m5['position_error_m']:.6f} m |

Repeatability: **PASS**

## AI Recovery-003 candidate

- Clean start: **PASS**
- Module status: **{ai['module_status']}**
- Independent verifier: **{ai['verifier_overall']}**
- Position error: **{ai['position_error_m']:.6f} m**
- Elapsed: **{ai['elapsed_time_s']:.3f} s**
- Initial distance to target: **{ai['initial_distance_to_target_m']:.3f} m**
- Telemetry rows: **{ai['telemetry_rows']}**

## Acceptance rule

The module's own success status is not the official verdict. Acceptance is based
on the independent deterministic verifier reading experiment evidence.

## Scope

This acceptance applies to the project's **PX4 SITL simulation PoC**.
"""
    (accepted_dir / "report.md").write_text(report_md, encoding="utf-8")

    print("=== NAV-001 ACCEPTANCE CLOSEOUT ===")
    print("STEP19_REPEATABILITY=PASS")
    print("RECOVERY003_FULL_TESTS=PASS")
    print("AI_SITL_CLEAN_START=PASS")
    print("AI_SITL_VERIFIER=PASS")
    print("STEP23_25=PASS")
    print("STEP30=PASS")
    print("CANDIDATE_STATUS=ACCEPTED")
    print("SCOPE=SIMULATION_ONLY")
    print(f"CANDIDATE_SHA256={candidate_hash}")
    print(f"ACCEPTED_DIR={accepted_dir}")
    print(f"STATUS={status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
