#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from evaluation.common import (
    PROJECT, LocalApi, describe, ensure_dir, environment_manifest, extract_attempt_row,
    source_manifest, stamp, wait_for_run, wait_for_session_commit, wilson_ci,
    write_csv, write_json,
)
from evaluation.direct_runtime import run_fresh_nav_trial, wait_flight_ready


def target_cycle(radius: float) -> list[list[float]]:
    r = float(radius)
    return [[r,0.0,-3.0],[0.0,r,-3.0],[-r,0.0,-3.0],[0.0,-r,-3.0],[r,r,-3.0],[-r,r,-3.0],[-r,-r,-3.0],[r,-r,-3.0]]


def summarize_condition(
    name: str,
    rows: list[dict[str, Any]],
    expected: str,
    *,
    requested_n: int,
    infra_exclusions: int,
) -> dict[str, Any]:
    n = len(rows)
    pass_count = sum(str(r.get("verifier_overall", "")).upper() == "PASS" for r in rows)
    expected_ok = pass_count if expected == "PASS" else n - pass_count
    lo, hi = wilson_ci(pass_count, n)
    elo, ehi = wilson_ci(expected_ok, n)
    errors = describe(r.get("position_error_m") for r in rows)
    elapsed = describe(r.get("elapsed_time_s") for r in rows)
    return {
        "Condition": name,
        "requested n": requested_n,
        "n": n,
        "Infrastructure exclusions": infra_exclusions,
        "Pass count": pass_count,
        "Pass rate": pass_count / n if n else None,
        "Pass 95% CI low": lo,
        "Pass 95% CI high": hi,
        "Expected outcome": expected,
        "Expected-behavior count": expected_ok,
        "Expected-behavior rate": expected_ok / n if n else None,
        "Expected 95% CI low": elo,
        "Expected 95% CI high": ehi,
        "e_pos mean": errors["mean"],
        "e_pos SD": errors["sd"],
        "e_pos median": errors["median"],
        "e_pos p95": errors["p95"],
        "duration mean s": elapsed["mean"],
        "duration p95 s": elapsed["p95"],
    }


async def direct_condition(
    name: str,
    *,
    trials: int,
    radius: float,
    out_rows: list[dict[str, Any]],
    infra_rows: list[dict[str, Any]],
    dropout: bool = False,
    timeout_s: float = 30.0,
    startup_retries: int = 3,
    startup_wait_s: float = 60.0,
    startup_stable_s: float = 2.0,
    startup_retry_cooldown_s: float = 3.0,
    max_infra_exclusions: int = 2,
) -> None:
    """Collect exactly ``trials`` valid capability samples, bounded by infra exclusions.

    A readiness failure happens before the capability evidence window opens, so it
    is not a NAV outcome.  It is recorded separately and the same requested sample
    is retried.  The exclusion budget prevents an unstable host from being hidden
    by unlimited retrying.
    """
    targets = target_cycle(radius)
    accepted = 0
    exclusions = 0
    launch_attempt = 0

    while accepted < trials:
        sample_no = accepted + 1
        target = targets[(sample_no - 1) % len(targets)]
        launch_attempt += 1
        scenario = f"EVAL-REL-{name.upper().replace(' ','_')}-{sample_no:03d}"
        print(
            f"REL_TRIAL_START condition={name} sample={sample_no}/{trials} "
            f"launch_attempt={launch_attempt} target={target}"
        )
        try:
            result = await run_fresh_nav_trial(
                scenario=scenario,
                target=target,
                nav_timeout_s=timeout_s,
                telemetry_dropout=dropout,
                startup_retries=startup_retries,
                startup_wait_s=startup_wait_s,
                startup_stable_s=startup_stable_s,
                startup_retry_cooldown_s=startup_retry_cooldown_s,
            )
        except Exception as exc:
            exclusions += 1
            infra = {
                "condition": name,
                "requested_sample": sample_no,
                "launch_attempt": launch_attempt,
                "scenario": scenario,
                "target": target,
                "exclusion_index": exclusions,
                "error": f"{type(exc).__name__}: {exc}",
            }
            infra_rows.append(infra)
            print(
                f"REL_INFRA_EXCLUDE condition={name} sample={sample_no}/{trials} "
                f"exclusion={exclusions}/{max_infra_exclusions} error={infra['error']}"
            )
            if exclusions > max_infra_exclusions:
                raise RuntimeError(
                    f"infrastructure exclusion budget exceeded for {name}: "
                    f"{exclusions}>{max_infra_exclusions}; collected={accepted}/{trials}"
                ) from exc
            if startup_retry_cooldown_s > 0:
                await asyncio.sleep(float(startup_retry_cooldown_s))
            continue

        nav = result.get("nav")
        if nav is None:
            raise RuntimeError("NAV result missing")
        accepted += 1
        row = extract_attempt_row(nav.attempt_dir, "NAV-001", condition=name)
        row.update({
            "trial": accepted,
            "scenario": scenario,
            "cleanup_error": result.get("cleanup_error"),
            "startup_attempts": result.get("startup_attempts"),
            "startup_ready": json.dumps(result.get("startup_ready"), sort_keys=True),
            "launch_attempt": launch_attempt,
            "infra_exclusions_before_sample": exclusions,
        })
        out_rows.append(row)
        print(
            f"REL_TRIAL_RESULT condition={name} trial={accepted}/{trials} "
            f"startup_attempts={row['startup_attempts']} module={row['module_status']} "
            f"verifier={row['verifier_overall']} err={row.get('position_error_m')}"
        )


async def continue_condition(
    base_url: str,
    flights: int,
    out_rows: list[dict[str, Any]],
    infra_rows: list[dict[str, Any]],
    *,
    startup_retries: int,
    startup_wait_s: float,
    startup_stable_s: float,
    startup_retry_cooldown_s: float,
) -> None:
    api = LocalApi(base_url)
    resources = api.get("/api/resources")
    if not resources.get("ok", False):
        raise RuntimeError(f"resource guard is not OK: {resources}")

    ready = None
    last_error: Exception | None = None
    startup_attempt = 0
    for startup_attempt in range(1, startup_retries + 1):
        try:
            session = api.post("/api/session/reset", {"start_position_m":[0.0,0.0,0.0]})
            print(
                f"REL_CONTINUE_RESET startup_attempt={startup_attempt} "
                f"state={session.get('state')} start={session.get('current_requested_start_position_m')}"
            )
            ready = await wait_flight_ready(
                timeout_s=startup_wait_s,
                stable_s=startup_stable_s,
            )
            print(f"REL_CONTINUE_SIM_READY startup_attempt={startup_attempt} state={ready}")
            break
        except Exception as exc:
            last_error = exc
            print(
                f"REL_CONTINUE_SIM_RETRY startup_attempt={startup_attempt}/{startup_retries} "
                f"error={type(exc).__name__}: {exc}"
            )
            if startup_attempt < startup_retries and startup_retry_cooldown_s > 0:
                await asyncio.sleep(startup_retry_cooldown_s)

    if ready is None:
        infra_rows.append({
            "condition": "CONTINUE-chained",
            "requested_sample": 0,
            "launch_attempt": startup_attempt,
            "scenario": "EVAL-REL-CONTINUE",
            "error": f"{type(last_error).__name__}: {last_error}" if last_error else "unknown readiness failure",
        })
        raise RuntimeError("CONTINUE simulator readiness failed; no CONTINUE sample counted") from last_error

    targets = target_cycle(5.0)
    for i in range(1, flights + 1):
        session = api.get("/api/session")
        start = session.get("current_requested_start_position_m")
        if not isinstance(start, list) or len(start) != 3:
            raise RuntimeError(f"invalid CONTINUE start: {start}")
        target = targets[(i-1) % len(targets)]
        payload = {
            "start_position_m": start,
            "target_position_m": target,
            "position_tolerance_m": 0.5,
            "start_position_tolerance_m": 0.75,
            "timeout_s": 30.0,
            "frame": "LOCAL_NED",
            "scenario_id": "EVAL-REL-CONTINUE",
        }
        t0 = time.perf_counter()
        queued = api.post("/api/experiment/start", payload)
        run = wait_for_run(api, queued["run_id"], timeout_s=60.0, poll_s=0.4)
        wall_s = time.perf_counter() - t0
        committed = wait_for_session_commit(api, queued["run_id"])
        result = run.get("result") or {}
        row = {
            "capability":"NAV-001",
            "condition":"CONTINUE-chained",
            "trial":i,
            "scenario":"EVAL-REL-CONTINUE",
            "run_id":queued["run_id"],
            "module_status":run.get("module_status") or "web-managed",
            "verifier_overall":result.get("overall") or run.get("status"),
            "position_error_m":result.get("position_error_m"),
            "elapsed_time_s":result.get("elapsed_time_s") or wall_s,
            "session_state":committed.get("state"),
            "api_wall_s":wall_s,
            "startup_attempts":startup_attempt,
            "classification":None,
        }
        out_rows.append(row)
        print(
            f"REL_CONTINUE {i}/{flights} verifier={row['verifier_overall']} "
            f"err={row['position_error_m']} state={row['session_state']}"
        )
        if str(row["verifier_overall"]).upper() != "PASS":
            break


async def run(args: argparse.Namespace) -> int:
    out = ensure_dir(Path(args.report_dir) / f"reliability_{stamp()}")
    write_json(out / "source_manifest.json", source_manifest())
    write_json(out / "environment.json", environment_manifest())
    rows: list[dict[str, Any]] = []
    infra_rows: list[dict[str, Any]] = []
    condition_failures: list[dict[str, str]] = []

    common = dict(
        startup_retries=args.startup_retries,
        startup_wait_s=args.startup_wait_s,
        startup_stable_s=args.startup_stable_s,
        startup_retry_cooldown_s=args.startup_retry_cooldown_s,
        max_infra_exclusions=args.max_infra_exclusions_per_condition,
    )

    async def guarded(name: str, action: Callable[[], Awaitable[None]]) -> None:
        try:
            await action()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            condition_failures.append({"condition": name, "error": reason})
            print(f"REL_CONDITION_ABORT condition={name} error={reason}")

    await guarded(
        "Fresh TAKEOFF, near targets",
        lambda: direct_condition(
            "Fresh TAKEOFF, near targets", trials=args.trials, radius=args.near_radius_m,
            out_rows=rows, infra_rows=infra_rows, **common,
        ),
    )
    await guarded(
        "Fresh TAKEOFF, far targets",
        lambda: direct_condition(
            "Fresh TAKEOFF, far targets", trials=args.trials, radius=args.far_radius_m,
            out_rows=rows, infra_rows=infra_rows, **common,
        ),
    )
    await guarded(
        "Injected telemetry dropout",
        lambda: direct_condition(
            "Injected telemetry dropout", trials=args.fault_trials, radius=args.near_radius_m,
            out_rows=rows, infra_rows=infra_rows, dropout=True, **common,
        ),
    )
    await guarded(
        "Injected timeout",
        lambda: direct_condition(
            "Injected timeout", trials=args.fault_trials, radius=args.far_radius_m,
            out_rows=rows, infra_rows=infra_rows, timeout_s=args.injected_timeout_s, **common,
        ),
    )

    if not args.skip_continue:
        print("REL_CONTINUE_NOTE web console must be running at " + args.base_url)
        await guarded(
            "CONTINUE-chained",
            lambda: continue_condition(
                args.base_url, args.continue_flights, rows, infra_rows,
                startup_retries=args.startup_retries,
                startup_wait_s=args.startup_wait_s,
                startup_stable_s=args.startup_stable_s,
                startup_retry_cooldown_s=args.startup_retry_cooldown_s,
            ),
        )

    expected = {
        "Fresh TAKEOFF, near targets":"PASS",
        "Fresh TAKEOFF, far targets":"PASS",
        "CONTINUE-chained":"PASS",
        "Injected telemetry dropout":"FAIL",
        "Injected timeout":"FAIL",
    }
    requested = {
        "Fresh TAKEOFF, near targets": args.trials,
        "Fresh TAKEOFF, far targets": args.trials,
        "CONTINUE-chained": 0 if args.skip_continue else args.continue_flights,
        "Injected telemetry dropout": args.fault_trials,
        "Injected timeout": args.fault_trials,
    }
    summary = []
    for name in expected:
        cond_rows = [r for r in rows if r.get("condition") == name]
        exclusions = sum(r.get("condition") == name for r in infra_rows)
        summary.append(
            summarize_condition(
                name, cond_rows, expected[name],
                requested_n=requested[name], infra_exclusions=exclusions,
            )
        )

    serial = []
    for r in rows:
        rr = dict(r)
        if isinstance(rr.get("checks"), (dict,list)):
            rr["checks"] = json.dumps(rr["checks"], sort_keys=True)
        if isinstance(rr.get("input"), (dict,list)):
            rr["input"] = json.dumps(rr["input"], sort_keys=True)
        serial.append(rr)
    write_csv(out / "reliability_runs.csv", serial)
    write_csv(out / "reliability_infrastructure.csv", infra_rows)
    write_csv(out / "reliability_summary.csv", summary)
    write_json(
        out / "reliability_summary.json",
        {"rows":summary, "condition_failures":condition_failures, "infrastructure":infra_rows},
    )

    complete_n = all(row["n"] == row["requested n"] for row in summary)
    expected_ok = all(
        (row["Expected-behavior rate"] == 1.0 if row["n"] else row["requested n"] == 0)
        for row in summary
    )
    within_infra_budget = all(
        row["Infrastructure exclusions"] <= args.max_infra_exclusions_per_condition
        for row in summary
    )
    overall = "PASS" if not condition_failures and complete_n and expected_ok and within_infra_budget else "FAIL"

    print(f"RELIABILITY_REPORT={out}")
    for row in summary:
        print(
            f"RELIABILITY_SUMMARY condition={row['Condition']} requested={row['requested n']} n={row['n']} "
            f"infra_exclusions={row['Infrastructure exclusions']} pass_rate={row['Pass rate']} "
            f"expected_behavior={row['Expected-behavior rate']} epos_mean={row['e_pos mean']} epos_p95={row['e_pos p95']}"
        )
    if condition_failures:
        print("RELIABILITY_CONDITION_FAILURES=" + json.dumps(condition_failures, sort_keys=True))
    print(f"RELIABILITY_RESULT={overall}")
    return 0 if overall == "PASS" else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Reliability and fault-injection evaluation")
    ap.add_argument("--trials", type=int, default=10, help="normal trials per fresh condition")
    ap.add_argument("--fault-trials", type=int, default=10)
    ap.add_argument("--continue-flights", type=int, default=20)
    ap.add_argument("--near-radius-m", type=float, default=3.0)
    ap.add_argument("--far-radius-m", type=float, default=10.0)
    ap.add_argument("--injected-timeout-s", type=float, default=0.5)
    ap.add_argument("--base-url", default="http://127.0.0.1:8370")
    ap.add_argument("--skip-continue", action="store_true")
    ap.add_argument("--startup-retries", type=int, default=3)
    ap.add_argument("--startup-wait-s", type=float, default=60.0)
    ap.add_argument("--startup-stable-s", type=float, default=2.0)
    ap.add_argument("--startup-retry-cooldown-s", type=float, default=3.0)
    ap.add_argument(
        "--max-infra-exclusions-per-condition", type=int, default=2,
        help="bounded number of pre-execution infrastructure exclusions allowed while still collecting requested n",
    )
    ap.add_argument("--report-dir", default=str(PROJECT / "reports" / "evaluation"))
    args = ap.parse_args()
    for v in (args.trials,args.fault_trials,args.continue_flights):
        if v < 0:
            raise SystemExit("trial counts must be >= 0")
    if args.startup_retries <= 0:
        raise SystemExit("--startup-retries must be > 0")
    if args.startup_wait_s <= 0:
        raise SystemExit("--startup-wait-s must be > 0")
    if args.startup_stable_s < 0:
        raise SystemExit("--startup-stable-s must be >= 0")
    if args.startup_retry_cooldown_s < 0:
        raise SystemExit("--startup-retry-cooldown-s must be >= 0")
    if args.max_infra_exclusions_per_condition < 0:
        raise SystemExit("--max-infra-exclusions-per-condition must be >= 0")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
