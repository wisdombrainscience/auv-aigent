#!/usr/bin/env python3
"""Automated long-session NAV-001 soak runner for the localhost web console.\n\nThe web console must already be running.  The runner intentionally drives the\nsame HTTP API as the UI so it exercises session continuity, verification,\nevidence creation and resource accounting together.\n\nThis tool is simulation-only.\n"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TERMINAL_STATUSES = {"PASS", "FAIL", "FAILED"}


@dataclass(frozen=True)
class SoakTarget:
    north: float
    east: float
    altitude: float

    def ned(self) -> list[float]:
        return [self.north, self.east, -abs(self.altitude)]


def target_cycle(radius_m: float = 5.0) -> list[SoakTarget]:
    """Small deterministic 3D loop that stays well inside the ±20 m region."""
    r = float(radius_m)
    return [
        SoakTarget(r, 0.0, 2.0),
        SoakTarget(r, r, 3.0),
        SoakTarget(0.0, r, 3.0),
        SoakTarget(-r, r, 2.0),
        SoakTarget(-r, 0.0, 3.0),
        SoakTarget(-r, -r, 2.0),
        SoakTarget(0.0, -r, 3.0),
        SoakTarget(r, -r, 2.0),
    ]


class LocalApi:
    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"cannot reach {self.base_url}: {exc}") from exc

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)


def wait_for_run(api: LocalApi, run_id: str, timeout_s: float, poll_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = api.get(f"/api/experiment/{run_id}/status")
        if str(run.get("status", "")).upper() in TERMINAL_STATUSES:
            return run
        time.sleep(poll_s)
    raise TimeoutError(f"run {run_id} did not finish within {timeout_s:g}s")


def wait_for_session_commit(api: LocalApi, run_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last = {}
    while time.monotonic() < deadline:
        last = api.get("/api/session")
        if last.get("last_run_id") == run_id:
            return last
        time.sleep(0.1)
    return last


def numeric(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def main() -> int:
    ap = argparse.ArgumentParser(description="NAV-001 continuous-flight soak test")
    ap.add_argument("--base-url", default="http://127.0.0.1:8370")
    ap.add_argument("--flights", type=int, default=50)
    ap.add_argument("--radius-m", type=float, default=5.0)
    ap.add_argument("--timeout-s", type=float, default=30.0, help="NAV-001 per-flight timeout")
    ap.add_argument("--run-wait-s", type=float, default=45.0, help="maximum wall time per API run")
    ap.add_argument("--poll-s", type=float, default=0.4)
    ap.add_argument("--reset", action="store_true", help="restart simulator at [0,0,2] before soak")
    ap.add_argument("--warmup-flights", type=int, default=5)
    ap.add_argument("--rss-warning-growth-mb", type=float, default=64.0)
    ap.add_argument("--report-dir", type=Path, default=Path("reports/soak"))
    args = ap.parse_args()

    if args.flights <= 0:
        raise SystemExit("--flights must be > 0")
    if args.radius_m <= 0 or args.radius_m > 10:
        raise SystemExit("--radius-m must be in (0, 10]")

    api = LocalApi(args.base_url)
    started = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "started_at": started.isoformat(),
        "base_url": args.base_url,
        "requested_flights": args.flights,
        "radius_m": args.radius_m,
        "flights": [],
        "resource_samples": [],
    }

    # Validate server before doing anything stateful.
    initial_resources = api.get("/api/resources")
    report["resource_samples"].append({"flight": 0, **initial_resources})
    if not initial_resources.get("ok", False):
        raise SystemExit(f"resource guard is not OK: {initial_resources}")

    if args.reset:
        session = api.post("/api/session/reset", {"start_position_m": [0.0, 0.0, -2.0]})
    else:
        session = api.get("/api/session")
        if not session.get("session_id") or session.get("state") in {"NEW", "RESET_REQUIRED"}:
            raise SystemExit("no READY/COMPLETED session; use --reset or Start / Restart Simulation first")

    targets = target_cycle(args.radius_m)
    pass_count = 0
    failed = False
    warmup_rss: float | None = None
    max_rss: float | None = numeric(initial_resources.get("process_rss_mb"))

    print(f"SOAK_START flights={args.flights} session={session.get('session_id')}")
    for idx in range(1, args.flights + 1):
        session = api.get("/api/session")
        if session.get("state") == "RESET_REQUIRED":
            print(f"[{idx:03d}] BLOCKED session=RESET_REQUIRED")
            failed = True
            break
        start = session.get("current_requested_start_position_m")
        if not isinstance(start, list) or len(start) != 3:
            print(f"[{idx:03d}] BLOCKED no valid current start")
            failed = True
            break

        target = targets[(idx - 1) % len(targets)].ned()
        payload = {
            "start_position_m": start,
            "target_position_m": target,
            "position_tolerance_m": 0.5,
            "start_position_tolerance_m": 0.75,
            "timeout_s": args.timeout_s,
            "frame": "LOCAL_NED",
            "scenario_id": "SOAK-V43",
        }
        queued = api.post("/api/experiment/start", payload)
        run_id = queued["run_id"]
        run = wait_for_run(api, run_id, args.run_wait_s, args.poll_s)
        committed = wait_for_session_commit(api, run_id)
        resources = api.get("/api/resources")
        report["resource_samples"].append({"flight": idx, **resources})

        verdict = (run.get("result") or {}).get("overall") or run.get("status")
        err = numeric((run.get("result") or {}).get("position_error_m"))
        rss = numeric(resources.get("process_rss_mb"))
        if rss is not None:
            max_rss = rss if max_rss is None else max(max_rss, rss)
        if idx == max(1, args.warmup_flights):
            warmup_rss = rss

        item = {
            "flight": idx,
            "run_id": run_id,
            "verdict": verdict,
            "position_error_m": err,
            "requested_start_position_m": payload["start_position_m"],
            "target_position_m": target,
            "final_position_m": run.get("verified_final_position_m"),
            "session_state": committed.get("state"),
            "process_rss_mb": rss,
            "open_fd_count": resources.get("open_fd_count"),
            "process_threads": resources.get("process_threads"),
            "mavsdk_server_process_count": resources.get("mavsdk_server_process_count"),
            "runs_mb": resources.get("runs_mb"),
        }
        report["flights"].append(item)
        print(
            f"[{idx:03d}/{args.flights:03d}] {verdict:<5} "
            f"err={err if err is not None else '—'}m "
            f"rss={rss if rss is not None else 'n/a'}MB "
            f"fds={resources.get('open_fd_count')} "
            f"mavsdk={resources.get('mavsdk_server_process_count')} "
            f"runs={resources.get('runs_mb')}MB"
        )

        if str(verdict).upper() != "PASS":
            failed = True
            reason = (run.get("result") or {}).get("reason") or run.get("error")
            phase = run.get("phase")
            print(
                f"SOAK_FAILURE phase={phase or 'n/a'} "
                f"reason={reason or 'n/a'}"
            )
            print(f"SOAK_STOP first non-PASS run={run_id}")
            break
        pass_count += 1

    final_resources = api.get("/api/resources")
    final_rss = numeric(final_resources.get("process_rss_mb"))
    rss_growth = None
    if warmup_rss is not None and final_rss is not None:
        rss_growth = round(final_rss - warmup_rss, 3)
    resource_cleanup_stable = all(
        sample.get("mavsdk_server_process_count") in (0, None)
        for sample in report["resource_samples"][1:]
    )
    report.update(
        {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "completed_flights": len(report["flights"]),
            "pass_count": pass_count,
            "all_pass": pass_count == args.flights and not failed,
            "warmup_rss_mb": warmup_rss,
            "final_rss_mb": final_rss,
            "max_rss_mb": max_rss,
            "rss_growth_after_warmup_mb": rss_growth,
            "rss_warning_growth_mb": args.rss_warning_growth_mb,
            "memory_stable": rss_growth is None or rss_growth <= args.rss_warning_growth_mb,
            "resource_cleanup_stable": resource_cleanup_stable,
            "final_resources": final_resources,
        }
    )
    report["overall"] = (
        "PASS"
        if report["all_pass"] and report["memory_stable"] and report["resource_cleanup_stable"] and final_resources.get("ok", False)
        else "WARN" if report["all_pass"] else "FAIL"
    )

    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    out = args.report_dir / f"nav001_soak_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"SOAK_RESULT={report['overall']} pass={pass_count}/{args.flights} "
        f"rss_growth_after_warmup={rss_growth}MB report={out}"
    )
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
