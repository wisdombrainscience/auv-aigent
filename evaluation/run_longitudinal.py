#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.runtime_paths import runtime_root
from evaluation.direct_runtime import wait_flight_ready
from orchestration.step37a_simulation import SimulationSupervisor
from evaluation.common import (
    PROJECT, LocalApi, ensure_dir, environment_manifest, finite, percentile,
    source_manifest, stamp, wait_for_run, wait_for_session_commit, write_csv, write_json,
)




def _api_reachable(api: LocalApi) -> bool:
    try:
        api.get("/api/resources")
        return True
    except Exception:
        return False


def ensure_local_console(api: LocalApi, base_url: str, timeout_s: float, log_path: Path) -> tuple[subprocess.Popen[str] | None, Any | None]:
    """Use an existing console or start one owned by this soak run."""
    if _api_reachable(api):
        print(f"SOAK_CONSOLE=EXISTING url={base_url}")
        return None, None

    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"console is unreachable at {base_url}; auto-start is only allowed for localhost HTTP"
        )
    port = parsed.port or 80
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("UAV_GAZEBO_GUI", "off")
    env["UAV_PORT"] = str(port)
    proc = subprocess.Popen(
        [str(PROJECT / "start-uav")],
        cwd=PROJECT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + float(timeout_s)
    last_error = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_handle.flush()
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"auto-started console exited code={proc.returncode}; tail={tail}")
        try:
            api.get("/api/resources")
            print(f"SOAK_CONSOLE=AUTO_STARTED pid={proc.pid} url={base_url}")
            return proc, log_handle
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    log_handle.close()
    raise RuntimeError(f"console did not become ready within {timeout_s:g}s: {last_error}")


def stop_owned_console(proc: subprocess.Popen[str] | None, log_handle: Any | None) -> None:
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if log_handle is not None:
        try:
            log_handle.close()
        except Exception:
            pass



def cleanup_owned_runtime(proc: subprocess.Popen[str] | None, log_handle: Any | None) -> None:
    """Stop only resources created by this soak process.

    v1.3 stopped the auto-started web console but left the managed PX4/Gazebo
    container alive.  That can consume CPU and confuse later smoke runs.  When
    the console was auto-started by this evaluator, it owns the simulator too.
    Existing user-started consoles/simulators are never stopped here.
    """
    owned = proc is not None
    stop_owned_console(proc, log_handle)
    if owned:
        try:
            SimulationSupervisor(PROJECT).stop()
            print("SOAK_CLEANUP simulator=STOPPED")
        except Exception as exc:
            print(f"SOAK_CLEANUP_WARNING simulator_stop={type(exc).__name__}: {exc}")

def target_cycle(radius: float = 5.0) -> list[list[float]]:
    r = float(radius)
    return [[r,0.0,-2.0],[r,r,-3.0],[0.0,r,-3.0],[-r,r,-2.0],[-r,0.0,-3.0],[-r,-r,-2.0],[0.0,-r,-3.0],[r,-r,-2.0]]


def _simulator_log_mb() -> float:
    path = runtime_root(PROJECT) / "simulator.log"
    try:
        return round(path.stat().st_size / (1024.0 * 1024.0), 3)
    except OSError:
        return 0.0


def milestone_rows(samples: list[dict[str, Any]], latency_events: list[dict[str, Any]], milestones: list[float]) -> list[dict[str, Any]]:
    out = []
    for minute in milestones:
        eligible = [s for s in samples if float(s.get("elapsed_min", 0.0)) >= minute]
        sample = eligible[0] if eligible else (samples[-1] if samples else {})
        lats = [float(e["latency_ms"]) for e in latency_events if float(e["elapsed_min"]) <= float(sample.get("elapsed_min", minute))]
        out.append({
            "t (min)": minute,
            "sample_t_min": sample.get("elapsed_min"),
            "RSS (MB)": sample.get("process_rss_mb"),
            "FDs": sample.get("open_fd_count"),
            "MAVSDK": sample.get("mavsdk_server_process_count"),
            "Storage (MB)": sample.get("runs_mb"),
            "Simulator log (MB)": sample.get("simulator_log_mb"),
            "p95 latency (ms)": percentile(lats, 0.95),
            "flights_completed": sample.get("flights_completed"),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Duration-based longitudinal resource soak via the localhost web API")
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--base-url", default="http://127.0.0.1:8370")
    ap.add_argument("--radius-m", type=float, default=5.0)
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--run-wait-s", type=float, default=60.0)
    ap.add_argument("--poll-s", type=float, default=0.4)
    ap.add_argument("--cycle-idle-s", type=float, default=1.0)
    ap.add_argument("--sample-period-s", type=float, default=15.0)
    ap.add_argument("--report-dir", default=str(PROJECT / "reports" / "evaluation"))
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--no-auto-start-console", action="store_true", help="require the web console to already be running")
    ap.add_argument("--console-start-timeout-s", type=float, default=30.0)
    ap.add_argument("--max-host-simlog-mb", type=float, default=64.0, help="abort if runs/.runtime/simulator.log exceeds this size")
    ap.add_argument("--startup-readiness-timeout-s", type=float, default=60.0)
    ap.add_argument("--startup-readiness-stable-s", type=float, default=2.0)
    ap.add_argument("--startup-readiness-retries", type=int, default=2)
    ap.add_argument("--startup-retry-cooldown-s", type=float, default=3.0)
    args = ap.parse_args()
    if args.hours <= 0:
        raise SystemExit("--hours must be > 0")

    out = ensure_dir(Path(args.report_dir) / f"longitudinal_{stamp()}")
    write_json(out / "source_manifest.json", source_manifest())
    write_json(out / "environment.json", environment_manifest())
    api = LocalApi(args.base_url)
    console_proc = None
    console_log_handle = None
    if args.no_auto_start_console:
        initial = api.get("/api/resources")
        print(f"SOAK_CONSOLE=REQUIRED_EXISTING url={args.base_url}")
    else:
        console_proc, console_log_handle = ensure_local_console(
            api, args.base_url, args.console_start_timeout_s, out / "console.log"
        )
        initial = api.get("/api/resources")
    if not initial.get("ok", False):
        cleanup_owned_runtime(console_proc, console_log_handle)
        raise SystemExit(f"resource guard not OK: {initial}")
    if args.startup_readiness_timeout_s <= 0:
        cleanup_owned_runtime(console_proc, console_log_handle)
        raise SystemExit("--startup-readiness-timeout-s must be > 0")
    if args.startup_readiness_stable_s < 0:
        cleanup_owned_runtime(console_proc, console_log_handle)
        raise SystemExit("--startup-readiness-stable-s must be >= 0")
    if args.startup_readiness_retries <= 0:
        cleanup_owned_runtime(console_proc, console_log_handle)
        raise SystemExit("--startup-readiness-retries must be > 0")
    if args.startup_retry_cooldown_s < 0:
        cleanup_owned_runtime(console_proc, console_log_handle)
        raise SystemExit("--startup-retry-cooldown-s must be >= 0")

    # A managed container being RUNNING is not yet a flight-readiness guarantee.
    # Real v1.3 smoke evidence showed local-position-valid followed by ten seconds
    # of arm() COMMAND_DENIED.  Establish the stronger, non-commanding PX4 health
    # readiness gate *before* t=0 so the longitudinal table measures steady-state
    # runtime/resource behavior rather than cold-boot estimator/home convergence.
    readiness = None
    readiness_error = None
    reset_state = None
    for startup_attempt in range(1, args.startup_readiness_retries + 1):
        try:
            if not args.no_reset:
                reset_state = api.post("/api/session/reset", {"start_position_m":[0.0,0.0,0.0]})
                print(
                    "SOAK_SESSION_RESET "
                    f"startup_attempt={startup_attempt} "
                    f"state={reset_state.get('state')} mode={reset_state.get('mode')} "
                    f"start={reset_state.get('current_requested_start_position_m')}"
                )
            readiness = asyncio.run(
                wait_flight_ready(
                    timeout_s=args.startup_readiness_timeout_s,
                    stable_s=args.startup_readiness_stable_s,
                )
            )
            print(
                f"SOAK_SIM_READY startup_attempt={startup_attempt} "
                f"state={readiness}"
            )
            break
        except Exception as exc:
            readiness_error = exc
            print(
                f"SOAK_SIM_RETRY startup_attempt={startup_attempt}/{args.startup_readiness_retries} "
                f"error={type(exc).__name__}: {exc}"
            )
            if startup_attempt < args.startup_readiness_retries:
                time.sleep(args.startup_retry_cooldown_s)
    if readiness is None:
        cleanup_owned_runtime(console_proc, console_log_handle)
        raise SystemExit(
            "simulation flight readiness failed before measurement: "
            f"{type(readiness_error).__name__}: {readiness_error}"
        )
    write_json(
        out / "startup_readiness.json",
        {
            "startup_attempts": startup_attempt,
            "reset_state": reset_state,
            "readiness": readiness,
        },
    )

    # Longitudinal measurement begins only after startup readiness has been
    # established. This makes t=0 reproducible and prevents cold-boot noise from
    # being confused with steady-state resource drift.
    started_wall = time.monotonic()
    deadline = started_wall + args.hours * 3600.0
    next_sample = started_wall
    flights = 0
    pass_count = 0
    samples: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    latency_events: list[dict[str, Any]] = []
    targets = target_cycle(args.radius_m)

    def sample_resources(force: bool = False) -> None:
        nonlocal next_sample
        now = time.monotonic()
        if not force and now < next_sample:
            return
        res, lat = api.get_timed("/api/resources")
        elapsed_min = (time.monotonic() - started_wall) / 60.0
        sim_log_mb = _simulator_log_mb()
        samples.append({"elapsed_min":elapsed_min,"flights_completed":flights,"simulator_log_mb":sim_log_mb,**res})
        latency_events.append({"elapsed_min":elapsed_min,"endpoint":"/api/resources","latency_ms":lat})
        next_sample = now + args.sample_period_s
        print(
            f"SOAK_RESOURCE t={elapsed_min:.1f}m flights={flights} "
            f"rss={res.get('process_rss_mb')} fds={res.get('open_fd_count')} "
            f"mavsdk={res.get('mavsdk_server_process_count')} storage={res.get('runs_mb')}MB "
            f"free={res.get('free_disk_mb')}MB simlog={sim_log_mb}MB latency={lat:.1f}ms"
        )
        if sim_log_mb > float(args.max_host_simlog_mb):
            raise RuntimeError(
                f"host simulator.log exceeded {args.max_host_simlog_mb:g} MB; "
                "set UAV_SIM_HOST_LOG_MODE=docker or fix log rotation before a long soak"
            )
        if not res.get("ok", False):
            raise RuntimeError(f"resource guard became not OK: {res}")

    sample_resources(force=True)
    failed_reason = None
    try:
        while time.monotonic() < deadline:
            session, lat = api.get_timed("/api/session")
            elapsed = (time.monotonic() - started_wall) / 60.0
            latency_events.append({"elapsed_min":elapsed,"endpoint":"/api/session","latency_ms":lat})
            if session.get("state") == "RESET_REQUIRED":
                raise RuntimeError("session entered RESET_REQUIRED")
            start = session.get("current_requested_start_position_m")
            if not isinstance(start, list) or len(start) != 3:
                raise RuntimeError(f"invalid session start: {start}")
            target = targets[flights % len(targets)]
            payload = {
                "start_position_m":start,
                "target_position_m":target,
                "position_tolerance_m":0.5,
                "start_position_tolerance_m":0.75,
                "timeout_s":args.timeout_s,
                "frame":"LOCAL_NED",
                "scenario_id":"EVAL-LONGITUDINAL-4H",
            }
            t0 = time.perf_counter()
            queued, post_lat = api.post_timed("/api/experiment/start", payload)
            latency_events.append({"elapsed_min":elapsed,"endpoint":"/api/experiment/start","latency_ms":post_lat})
            run = wait_for_run(api, queued["run_id"], timeout_s=args.run_wait_s, poll_s=args.poll_s)
            committed = wait_for_session_commit(api, queued["run_id"])
            wall_s = time.perf_counter() - t0
            flights += 1
            result = run.get("result") or {}
            verdict = result.get("overall") or run.get("status")
            if str(verdict).upper() == "PASS":
                pass_count += 1
            run_phase = run.get("phase")
            run_reason = result.get("reason") or run.get("error")
            attempt_dir = run.get("attempt_dir")
            runs.append({
                "flight":flights,
                "elapsed_min":(time.monotonic()-started_wall)/60.0,
                "run_id":queued["run_id"],
                "verdict":verdict,
                "phase":run_phase,
                "reason":run_reason,
                "attempt_dir":attempt_dir,
                "position_error_m":result.get("position_error_m"),
                "elapsed_time_s":result.get("elapsed_time_s"),
                "wall_time_s":wall_s,
                "session_state":committed.get("state"),
                "target":json.dumps(target),
            })
            print(
                f"SOAK_FLIGHT {flights} verdict={verdict} phase={run_phase} "
                f"err={result.get('position_error_m')} reason={run_reason!r} wall={wall_s:.2f}s"
            )
            sample_resources(force=True)
            if str(verdict).upper() != "PASS":
                write_json(out / "first_nonpass_status.json", run)
                write_json(out / "first_nonpass_session.json", committed)
                print(
                    f"SOAK_NONPASS_DETAIL run_id={queued['run_id']} phase={run_phase} "
                    f"reason={run_reason!r} attempt_dir={attempt_dir} session={committed.get('state')}"
                )
                raise RuntimeError(
                    f"non-PASS flight {queued['run_id']}: {verdict}; phase={run_phase}; reason={run_reason}"
                )
            if args.cycle_idle_s > 0:
                time.sleep(args.cycle_idle_s)
                sample_resources()
    except Exception as exc:
        failed_reason = f"{type(exc).__name__}: {exc}"
        print(f"SOAK_ABORT {failed_reason}")
    finally:
        try:
            sample_resources(force=True)
        except Exception:
            pass

    cleanup_owned_runtime(console_proc, console_log_handle)

    elapsed_h = (time.monotonic() - started_wall) / 3600.0
    milestones = [0.0, 60.0, 120.0, 240.0]
    if args.hours * 60.0 > 240.0:
        milestones.append(args.hours * 60.0)
    table = milestone_rows(samples, latency_events, milestones)
    write_csv(out / "longitudinal_samples.csv", samples)
    write_csv(out / "longitudinal_runs.csv", runs)
    write_csv(out / "latency_events.csv", latency_events)
    write_csv(out / "longitudinal_milestones.csv", table)
    summary = {
        "requested_hours":args.hours,
        "elapsed_hours":elapsed_h,
        "flights_completed":flights,
        "pass_count":pass_count,
        "all_flights_pass":pass_count == flights,
        "completed_requested_duration": elapsed_h >= args.hours * 0.999,
        "failed_reason":failed_reason,
        "milestones":table,
        "overall":"PASS" if failed_reason is None and elapsed_h >= args.hours * 0.999 and pass_count == flights else "FAIL",
    }
    write_json(out / "longitudinal_summary.json", summary)
    print(f"LONGITUDINAL_REPORT={out}")
    print(f"LONGITUDINAL_RESULT={summary['overall']} elapsed_hours={elapsed_h:.3f} flights={flights} pass={pass_count}")
    return 0 if summary["overall"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
