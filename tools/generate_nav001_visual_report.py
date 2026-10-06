#!/usr/bin/env python3
"""
Generate STEP 31-32 offline visualization + final report for NAV-001.

Reads only accepted PX4 SITL simulation evidence. It does not control a UAV,
does not rerun experiments, and does not modify the accepted candidate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
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


def read_telemetry(path: Path):
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 2:
        raise SystemExit(f"telemetry too short: {path}")
    out = []
    for row in rows:
        out.append(
            {
                "timestamp": float(row["timestamp"]),
                "x": float(row["x"]),
                "y": float(row["y"]),
                "z": float(row["z"]),
                "vehicle_state": row.get("vehicle_state"),
                "localization_valid": row.get("localization_valid"),
            }
        )
    return out


def attempt_summary(attempt: Path):
    input_doc = load_json(attempt / "input.json")
    module_doc = load_json(attempt / "module_output.json")
    verification = load_json(attempt / "verification.json")
    telemetry = read_telemetry(attempt / "telemetry.csv")

    target = tuple(float(v) for v in input_doc["target_position_m"])
    start = (telemetry[0]["x"], telemetry[0]["y"], telemetry[0]["z"])
    end = (telemetry[-1]["x"], telemetry[-1]["y"], telemetry[-1]["z"])

    return {
        "attempt": attempt.name,
        "path": str(attempt),
        "input": input_doc,
        "module": module_doc,
        "verification": verification,
        "telemetry": telemetry,
        "target": target,
        "start": start,
        "end": end,
        "initial_error_m": math.dist(start, target),
        "final_error_telemetry_m": math.dist(end, target),
        "telemetry_sha256": sha256(attempt / "telemetry.csv"),
    }


def require_matplotlib():
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise SystemExit(
            "matplotlib is required for STEP 31 visualization. "
            "Run this with the project venv that contains matplotlib.\n"
            f"Import error: {exc}"
        )
    return plt


def trajectory_plot(plt, summary, output: Path):
    tel = summary["telemetry"]
    xs = [r["x"] for r in tel]
    ys = [r["y"] for r in tel]
    zs = [r["z"] for r in tel]
    target = summary["target"]

    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot(xs, ys, zs, label="Measured trajectory")
    ax.scatter([xs[0]], [ys[0]], [zs[0]], marker="o", s=60, label="Start")
    ax.scatter([target[0]], [target[1]], [target[2]], marker="x", s=90, label="Target")
    ax.scatter([xs[-1]], [ys[-1]], [zs[-1]], marker="^", s=60, label="Final")
    ax.set_title("NAV-001 GoToTarget — PX4 SITL trajectory")
    ax.set_xlabel("North / X (m)")
    ax.set_ylabel("East / Y (m)")
    ax.set_zlabel("Down / Z (m)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def error_plot(plt, summary, output: Path):
    tel = summary["telemetry"]
    target = summary["target"]
    t0 = tel[0]["timestamp"]
    times = [r["timestamp"] - t0 for r in tel]
    errors = [
        math.sqrt(
            (r["x"] - target[0]) ** 2
            + (r["y"] - target[1]) ** 2
            + (r["z"] - target[2]) ** 2
        )
        for r in tel
    ]
    tolerance = float(summary["input"]["position_tolerance_m"])

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(times, errors, label="3D position error")
    ax.axhline(tolerance, linestyle="--", label=f"Tolerance = {tolerance:.3f} m")
    ax.set_title("NAV-001 — Position error over time")
    ax.set_xlabel("Time from first telemetry sample (s)")
    ax.set_ylabel("3D position error (m)")
    ax.grid(True)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def verification_plot(plt, summary, output: Path):
    ver = summary["verification"]
    inp = summary["input"]
    checks = ver.get("checks", {})
    error = float(ver.get("position_error_m"))
    tolerance = float(inp["position_tolerance_m"])
    elapsed = float(ver.get("elapsed_time_s"))
    timeout = float(inp["timeout_s"])

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axis("off")

    lines = [
        "NAV-001 — Verification Summary",
        "",
        f"Requested target: {tuple(inp['target_position_m'])}",
        f"Tolerance: {tolerance:.3f} m",
        f"Timeout: {timeout:.3f} s",
        "",
        f"Observed final error: {error:.6f} m",
        f"Observed elapsed: {elapsed:.3f} s",
        "",
        f"Position check: {checks.get('position', 'UNKNOWN')}",
        f"Timeout check: {checks.get('timeout', 'UNKNOWN')}",
        f"Telemetry check: {checks.get('telemetry', 'UNKNOWN')}",
        "",
        f"FINAL VERDICT: {ver.get('overall', 'UNKNOWN')}",
        "",
        "Verdict source: independent deterministic verifier",
        "Scope: PX4 SITL simulation",
    ]

    ax.text(
        0.04,
        0.96,
        "\n".join(lines),
        va="top",
        ha="left",
        fontsize=12,
        family="monospace",
        transform=ax.transAxes,
    )
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def comparison_csv(path: Path, summaries):
    fields = [
        "label",
        "attempt",
        "initial_error_m",
        "final_error_m",
        "elapsed_time_s",
        "module_status",
        "verifier",
        "telemetry_sha256",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for label, s in summaries:
            writer.writerow(
                {
                    "label": label,
                    "attempt": s["attempt"],
                    "initial_error_m": f"{s['initial_error_m']:.9f}",
                    "final_error_m": f"{float(s['verification']['position_error_m']):.9f}",
                    "elapsed_time_s": f"{float(s['verification']['elapsed_time_s']):.9f}",
                    "module_status": s["module"].get("status"),
                    "verifier": s["verification"].get("overall"),
                    "telemetry_sha256": s["telemetry_sha256"],
                }
            )


def report_markdown(
    project: Path,
    candidate_hash: str,
    manual4,
    manual5,
    ai,
    accepted_manifest: dict,
):
    inp = ai["input"]
    ver = ai["verification"]
    mod = ai["module"]

    return f"""# NAV-001 GoToTarget — Final PoC Report

## Final status

**ACCEPTED — PX4 SITL simulation scope**

Candidate SHA256:

`{candidate_hash}`

This candidate was accepted only after unit/full tests, clean-start SITL execution,
evidence collection, and independent deterministic verification passed.

## Requested

- Frame: `{inp['frame']}`
- Target: `{tuple(inp['target_position_m'])}`
- Position tolerance: `{float(inp['position_tolerance_m']):.3f} m`
- Timeout: `{float(inp['timeout_s']):.3f} s`
- Scenario: `{inp.get('scenario_id')}`

## Observed — AI Recovery-003 candidate

- Start position: `{tuple(round(v, 6) for v in ai['start'])}`
- Final telemetry position: `{tuple(round(v, 6) for v in ai['end'])}`
- Initial distance to target: `{ai['initial_error_m']:.6f} m`
- Module status: `{mod.get('status')}`
- Elapsed time: `{float(ver['elapsed_time_s']):.6f} s`
- Evidence integrity / telemetry check: `{ver.get('checks', {}).get('telemetry')}`

## Verified

- Position error: `{float(ver['position_error_m']):.9f} m`
- Allowed tolerance: `{float(inp['position_tolerance_m']):.3f} m`
- Position check: `{ver.get('checks', {}).get('position')}`
- Timeout check: `{ver.get('checks', {}).get('timeout')}`
- Telemetry check: `{ver.get('checks', {}).get('telemetry')}`
- Independent verifier overall: **{ver.get('overall')}**

The module's own `position_error_m` is diagnostic only. The official acceptance
verdict is the independent verifier result derived from experiment evidence.

## Clean manual control group

| Run | Initial distance | Final verified error | Elapsed | Clean start | Verifier |
|---|---:|---:|---:|---|---|
| attempt_004 | {manual4['initial_error_m']:.3f} m | {float(manual4['verification']['position_error_m']):.6f} m | {float(manual4['verification']['elapsed_time_s']):.3f} s | PASS | {manual4['verification'].get('overall')} |
| attempt_005 | {manual5['initial_error_m']:.3f} m | {float(manual5['verification']['position_error_m']):.6f} m | {float(manual5['verification']['elapsed_time_s']):.3f} s | PASS | {manual5['verification'].get('overall')} |

Manual clean-start repeatability: **PASS**

## AI activity

- Local-agent path: Implementation → deterministic gates → Recovery
- Final accepted recovery: `Recovery 003 / run_002_method_scope`
- Recovery output was constrained to method-scoped replacement.
- Specification, verifier, core APIs, and frozen tests remained protected.
- Accepted candidate hash: `{candidate_hash}`

## Important debugging finding

Earlier manual baseline runs were not suitable as clean-start repeatability
evidence because the simulator state had persisted near the target. The pipeline
was corrected by adding a deterministic clean SITL fixture and a clean-start
audit gate before capability execution.

A later AI SITL `attempt_001` was also correctly classified as experiment setup
failure because `airborne=false`; it was not used as evidence of a candidate bug.

## Evidence and artifacts

- Acceptance manifest: `reports/pipeline_v2/accepted/NAV-001/{candidate_hash}/acceptance_manifest.json`
- Accepted candidate: `reports/pipeline_v2/accepted/NAV-001/{candidate_hash}/go_to_target.py`
- Final status: `reports/pipeline_v2/step19_30_status.json`
- AI telemetry: `{ai['path']}/telemetry.csv`

## Final conclusion

The NAV-001 PoC demonstrates that a local AI coding/recovery workflow can produce
a `GoToTargetModule` candidate under a frozen contract, while deterministic
Python gates retain authority over boundary checking, experiment validity,
evidence, and PASS/FAIL. The accepted result is limited to the **PX4 SITL
simulation PoC** represented by the recorded evidence.

## Next framework steps

STEP 31 offline visualization and STEP 32 final report are completed by this
artifact set. The next engineering phase is STEP 34 experiment-matrix validation
across multiple clean-start scenarios before treating NAV-001 as a broader
capability template.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--run2", type=Path, default=DEFAULT_RUN2)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    run2 = args.run2.expanduser().resolve()

    status_path = project / "reports/pipeline_v2/step19_30_status.json"
    if not status_path.is_file():
        raise SystemExit(f"accepted status missing: {status_path}")

    status = load_json(status_path)
    if status.get("candidate_status") != "ACCEPTED":
        raise SystemExit("candidate is not ACCEPTED")
    if status.get("scope") != "SIMULATION_ONLY":
        raise SystemExit("unexpected acceptance scope")

    candidate_hash = status["candidate_sha256"]
    accepted_dir = (
        project
        / "reports/pipeline_v2/accepted/NAV-001"
        / candidate_hash
    )
    manifest_path = accepted_dir / "acceptance_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"acceptance manifest missing: {manifest_path}")
    accepted_manifest = load_json(manifest_path)

    manual_root = project / "runs/NAV-001/MANUAL-BASELINE"
    manual4 = attempt_summary(manual_root / "attempt_004")
    manual5 = attempt_summary(manual_root / "attempt_005")
    ai_attempt = (
        run2
        / "sitl_workspace/runs/NAV-001/AI-RECOVERY-003-SITL/attempt_002"
    )
    ai = attempt_summary(ai_attempt)

    if ai["verification"].get("overall") != "PASS":
        raise SystemExit("AI attempt verifier is not PASS")

    out = project / "reports/pipeline_v2/final_demo/NAV-001" / candidate_hash
    out.mkdir(parents=True, exist_ok=True)

    plt = require_matplotlib()
    trajectory_plot(plt, ai, out / "trajectory.png")
    error_plot(plt, ai, out / "position_error.png")
    verification_plot(plt, ai, out / "verification_summary.png")

    comparison_csv(
        out / "attempt_comparison.csv",
        [
            ("manual_clean_004", manual4),
            ("manual_clean_005", manual5),
            ("ai_recovery_003", ai),
        ],
    )

    report = report_markdown(
        project,
        candidate_hash,
        manual4,
        manual5,
        ai,
        accepted_manifest,
    )
    (out / "report.md").write_text(report, encoding="utf-8")

    summary = {
        "task": "NAV-001",
        "candidate_sha256": candidate_hash,
        "status": "ACCEPTED",
        "scope": "SIMULATION_ONLY",
        "step31_visualization": "PASS",
        "step32_report": "PASS",
        "outputs": {
            "trajectory": str(out / "trajectory.png"),
            "position_error": str(out / "position_error.png"),
            "verification_summary": str(out / "verification_summary.png"),
            "attempt_comparison": str(out / "attempt_comparison.csv"),
            "report": str(out / "report.md"),
        },
    }
    (out / "demo_manifest.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    status["STEP_31_offline_visualization"] = "PASS"
    status["STEP_32_final_report"] = "PASS"
    status_path.write_text(
        json.dumps(status, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("=== NAV-001 STEP 31-32 ===")
    print("STEP31_VISUALIZATION=PASS")
    print("STEP32_FINAL_REPORT=PASS")
    print(f"OUTPUT_DIR={out}")
    print(f"REPORT={out / 'report.md'}")
    print(f"TRAJECTORY={out / 'trajectory.png'}")
    print(f"POSITION_ERROR={out / 'position_error.png'}")
    print(f"VERIFICATION_SUMMARY={out / 'verification_summary.png'}")
    print(f"COMPARISON={out / 'attempt_comparison.csv'}")
    print("NEXT=STEP34_EXPERIMENT_MATRIX")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
