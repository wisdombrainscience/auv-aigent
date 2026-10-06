#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validation_status(path: Path):
    if not path.is_file():
        return "NOT_RUN"
    doc = load_json(path)
    return doc.get("overall", "UNKNOWN")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    resolved = load_json(root / "resolved_models.json")["models"]

    rows = []
    for model in resolved:
        run = root / "models" / model["id"] / "run_001"
        gen_path = run / "generation_result.json"
        if not gen_path.is_file():
            gen = {}
        else:
            gen = load_json(gen_path)

        targeted = validation_status(run / "candidate_validation_targeted.json")
        full = validation_status(run / "candidate_validation_full.json")

        first_attempt = (
            "PASS"
            if gen.get("candidate_created") and full == "PASS"
            else "FAIL"
            if gen_path.is_file()
            else "NOT_RUN"
        )

        rows.append({
            "model_id": model["id"],
            "ollama_tag": model["ollama_tag"],
            "opencode_model": model["opencode_model"],
            "agent_duration_s": gen.get("agent_duration_s"),
            "provider_exit_code": gen.get("provider_exit_code"),
            "generation_protocol": gen.get("generation_protocol", "NOT_RUN"),
            "candidate_created": gen.get("candidate_created", False),
            "targeted_tests": targeted,
            "full_tests": full,
            "first_attempt_acceptance": first_attempt,
            "sitl": "NOT_RUN_PHASE_36A",
            "final_verified_correctness": "NOT_EVALUATED_PHASE_36A",
        })

    out = project / "reports/pipeline_v2/step36_benchmark"
    out.mkdir(parents=True, exist_ok=True)

    csv_path = out / "phase_a_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    completed = sum(r["generation_protocol"] != "NOT_RUN" for r in rows)
    generated = sum(bool(r["candidate_created"]) for r in rows)
    targeted_pass = sum(r["targeted_tests"] == "PASS" for r in rows)
    full_pass = sum(r["full_tests"] == "PASS" for r in rows)

    summary = {
        "task": "NAV-001",
        "step": "36A",
        "scope": "IMPLEMENTATION_FIRST_ATTEMPT_ONLY",
        "models_total": len(rows),
        "models_run": completed,
        "candidate_created": generated,
        "targeted_tests_pass": targeted_pass,
        "full_tests_pass": full_pass,
        "phase_a_complete": completed == len(rows),
        "sitl_run": False,
        "recovery_evaluated": False,
        "rows": rows,
    }
    (out / "phase_a_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    md = [
        "# NAV-001 STEP 36A — Local Model Benchmark",
        "",
        "Phase A compares first-attempt implementation generation under the same",
        "frozen prompt, validator, boundary policy and test suite. SITL and recovery",
        "are intentionally not scored yet.",
        "",
        "| Model | Generation | Targeted | Full | First attempt | Duration |",
        "|---|---|---|---|---|---:|",
    ]
    for r in rows:
        dur = (
            f"{float(r['agent_duration_s']):.2f}s"
            if r["agent_duration_s"] is not None
            else "—"
        )
        md.append(
            f"| `{r['ollama_tag']}` | {r['generation_protocol']} | "
            f"{r['targeted_tests']} | {r['full_tests']} | "
            f"{r['first_attempt_acceptance']} | {dur} |"
        )

    md += [
        "",
        f"- Models run: {completed}/{len(rows)}",
        f"- Candidate created: {generated}/{len(rows)}",
        f"- Targeted tests PASS: {targeted_pass}/{len(rows)}",
        f"- Full tests PASS: {full_pass}/{len(rows)}",
        "",
        "No model ranking or final benchmark conclusion is produced until Phase 36B",
        "adds controlled recovery and verified PX4 SITL evidence for eligible candidates.",
    ]
    (out / "phase_a_report.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8"
    )

    print("=== NAV-001 STEP 36A AGGREGATE ===")
    print(f"MODELS_RUN={completed}/{len(rows)}")
    print(f"CANDIDATE_CREATED={generated}/{len(rows)}")
    print(f"TARGETED_PASS={targeted_pass}/{len(rows)}")
    print(f"FULL_PASS={full_pass}/{len(rows)}")
    print(f"PHASE_A_COMPLETE={'YES' if completed == len(rows) else 'NO'}")
    print(f"REPORT={out / 'phase_a_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
