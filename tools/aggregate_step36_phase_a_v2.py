#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def val_status(path: Path):
    if not path.is_file():
        return "NOT_RUN"
    return load_json(path).get("overall", "UNKNOWN")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    cfg = load_json(project / "benchmark/step36_models.json")["models"]

    rows = []
    for model in cfg:
        run = root / "models" / model["id"] / "run_002_semantic"
        genp = run / "generation_result.json"
        gen = load_json(genp) if genp.is_file() else {}
        targ = val_status(run / "candidate_validation_targeted.json")
        full = val_status(run / "candidate_validation_full.json")
        first = "PASS" if gen.get("candidate_created") and full == "PASS" else ("FAIL" if genp.is_file() else "NOT_RUN")
        rows.append({
            "model_id": model["id"],
            "ollama_tag": model["ollama_tag"],
            "semantic_run": "run_002_semantic",
            "generation_protocol": gen.get("generation_protocol", "NOT_RUN"),
            "candidate_created": gen.get("candidate_created", False),
            "targeted_tests": targ,
            "full_tests": full,
            "first_attempt_acceptance": first,
            "agent_duration_s": gen.get("agent_duration_s"),
            "classification": gen.get("classification", "NOT_RUN"),
            "sitl": "NOT_RUN_PHASE_36A",
        })

    out = project / "reports/pipeline_v2/step36_benchmark"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "phase_a_v2_results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    complete = sum(r["generation_protocol"] != "NOT_RUN" for r in rows)
    created = sum(bool(r["candidate_created"]) for r in rows)
    targeted = sum(r["targeted_tests"] == "PASS" for r in rows)
    full = sum(r["full_tests"] == "PASS" for r in rows)

    summary = {
        "task": "NAV-001",
        "step": "36A-v2",
        "models_total": len(rows),
        "models_run": complete,
        "candidate_created": created,
        "targeted_pass": targeted,
        "full_pass": full,
        "phase_a_complete": complete == len(rows),
        "run_001_infrastructure_evidence_preserved": True,
        "sitl_run": False,
        "rows": rows,
    }
    (out / "phase_a_v2_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("=== NAV-001 STEP 36A V2 AGGREGATE ===")
    print(f"MODELS_RUN={complete}/{len(rows)}")
    print(f"CANDIDATE_CREATED={created}/{len(rows)}")
    print(f"TARGETED_PASS={targeted}/{len(rows)}")
    print(f"FULL_PASS={full}/{len(rows)}")
    print(f"PHASE_A_COMPLETE={'YES' if complete == len(rows) else 'NO'}")
    print(f"CSV={out / 'phase_a_v2_results.csv'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
