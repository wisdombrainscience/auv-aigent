#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TERMINAL_STATUSES = {"PASS", "FAIL", "FAILED"}


def finite3(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must be a 3-element array")
    out = [float(v) for v in value]
    if not all(math.isfinite(v) for v in out):
        raise ValueError(f"{name} must contain finite numbers")
    return out


def build_payload() -> dict[str, Any]:
    return {
        "target_position_m": [5.0, 0.0, -3.0],
        "position_tolerance_m": 0.5,
        "start_position_tolerance_m": 0.75,
        "timeout_s": 30.0,
        "frame": "LOCAL_NED",
        "scenario_id": "CP0-SINGLE-FLIGHT-GROUND",
    }


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def configured_uav_port(project: Path) -> int:
    """Read the port actually used by the deployment wrapper.

    The v1.0.1 deployment wrapper sources ``uav.env`` and may let UAV_PORT
    override ``uavctl start --port``.  CP0 must probe the port the product
    actually starts on, rather than assuming the CLI override won.
    """
    port = 8370
    env_path = project / "uav.env"
    if not env_path.is_file():
        return port

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if not line.startswith("UAV_PORT="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]
        try:
            candidate = int(value)
        except ValueError as exc:
            raise ValueError(f"invalid UAV_PORT in {env_path}: {value!r}") from exc
        if not (1 <= candidate <= 65535):
            raise ValueError(f"UAV_PORT out of range in {env_path}: {candidate}")
        return candidate
    return port


def tcp_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


class LocalApi:
    def __init__(self, base_url: str, timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"cannot reach {self.base_url}: {exc}") from exc

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)


def wait_for_server(api: LocalApi, process: subprocess.Popen[Any], timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_s)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        rc = process.poll()
        if rc is not None:
            raise RuntimeError(f"Remote UAV server exited before readiness, returncode={rc}")
        try:
            status = api.get("/api/resources")
            return status
        except Exception as exc:  # server may still be starting
            last_error = exc
            time.sleep(0.25)
    raise TimeoutError(f"Remote UAV server did not become ready: {last_error}")


def wait_for_run(api: LocalApi, run_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_s)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.get(f"/api/experiment/{run_id}/status")
        status = str(last.get("status", "")).upper()
        phase = str(last.get("phase", "UNKNOWN"))
        print(f"CP0_SINGLE_STATUS status={status or 'UNKNOWN'} phase={phase}", flush=True)
        if status in TERMINAL_STATUSES:
            return last
        time.sleep(0.5)
    raise TimeoutError(f"single-flight run {run_id} did not finish; last={last}")


def server_stop(process: subprocess.Popen[Any], timeout_s: float = 20.0) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=5.0)


def active_docker_containers() -> set[str]:
    try:
        cp = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if cp.returncode != 0:
        return set()
    return {line.strip() for line in cp.stdout.splitlines() if line.strip()}


def mavsdk_process_lines() -> list[str]:
    try:
        cp = subprocess.run(
            ["pgrep", "-af", "mavsdk_server"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if cp.returncode not in (0, 1):
        return []
    # Exclude the pgrep command itself if an implementation ever echoes it.
    return [line.strip() for line in cp.stdout.splitlines() if line.strip() and "pgrep -af mavsdk_server" not in line]


def wait_for_cleanup(container_name: str | None, timeout_s: float = 15.0) -> tuple[bool, list[str]]:
    deadline = time.monotonic() + timeout_s
    diagnostics: list[str] = []
    while time.monotonic() < deadline:
        containers = active_docker_containers()
        mavsdk = mavsdk_process_lines()
        container_active = bool(container_name and container_name in containers)
        if not container_active and not mavsdk:
            return True, []
        diagnostics = []
        if container_active:
            diagnostics.append(f"container_still_running:{container_name}")
        diagnostics.extend(f"mavsdk_process:{line}" for line in mavsdk)
        time.sleep(0.5)
    return False, diagnostics


def validate_terminal(run: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    result = run.get("result") or {}
    verdict = str(result.get("overall") or run.get("status") or "").upper()
    if verdict != "PASS":
        errors.append(f"verdict_not_pass:{verdict or 'UNKNOWN'}")
    if run.get("phase") != "VERIFIED":
        errors.append(f"phase_not_verified:{run.get('phase')}")
    if run.get("verdict_authority") != "independent_python_verifier":
        errors.append("wrong_verdict_authority")
    if run.get("ai_runtime_control") is not False:
        errors.append("ai_runtime_control_not_false")
    if run.get("auto_takeoff_bootstrap") is not True:
        errors.append("ground_bootstrap_not_used")
    requested_ground = run.get("requested_session_start_position_m")
    try:
        if finite3(requested_ground, "requested_session_start_position_m") != [0.0, 0.0, 0.0]:
            errors.append(f"requested_ground_not_origin:{requested_ground}")
    except Exception:
        errors.append(f"invalid_requested_ground:{requested_ground}")
    checks = result.get("checks") or {}
    for check_name in ("position", "timeout", "telemetry"):
        if checks.get(check_name) != "PASS":
            errors.append(f"verification_check_failed:{check_name}={checks.get(check_name)}")
    if run.get("adapter_cleanup_error") not in (None, ""):
        errors.append(f"adapter_cleanup_error:{run.get('adapter_cleanup_error')}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="CP0 Single Flight physical-ground bootstrap acceptance")
    parser.add_argument("--project", default=".")
    parser.add_argument("--server-start-timeout-s", type=float, default=30.0)
    parser.add_argument("--run-wait-s", type=float, default=120.0)
    parser.add_argument("--cleanup-timeout-s", type=float, default=15.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        payload = build_payload()
        assert payload["target_position_m"] == [5.0, 0.0, -3.0]
        assert payload["scenario_id"] == "CP0-SINGLE-FLIGHT-GROUND"
        assert configured_uav_port(Path("/definitely/not/a/project")) == 8370
        print("CP0_SINGLE_FLIGHT_SELF_TEST=PASS")
        return 0

    project = Path(args.project).expanduser().resolve()
    uavctl = project / "uavctl"
    if not project.is_dir():
        raise SystemExit(f"project not found: {project}")
    if not uavctl.is_file():
        raise SystemExit(f"uavctl not found: {uavctl}")

    port = configured_uav_port(project)
    base_url = f"http://127.0.0.1:{port}"
    if tcp_port_in_use(port):
        raise SystemExit(
            f"CP0 single-flight port {port} is already in use; stop the existing "
            "Remote UAV console before running CP0"
        )
    # Use the deployment wrapper exactly as operators do.  The v1.0.1 wrapper
    # sources uav.env; probing the configured port avoids the known CLI/env
    # precedence mismatch where --port can be overwritten back to UAV_PORT.
    command = [str(uavctl), "start", "--headless"]
    print(f"CP0_SINGLE_PORT={port}", flush=True)
    print("CP0_SINGLE_SERVER=" + " ".join(command), flush=True)

    process = subprocess.Popen(
        command,
        cwd=project,
        start_new_session=True,
    )
    container_name: str | None = None
    errors: list[str] = []

    try:
        api = LocalApi(base_url)
        resources_before = wait_for_server(api, process, args.server_start_timeout_s)
        print("CP0_SINGLE_RESOURCE_BEFORE=" + json.dumps(resources_before, separators=(",", ":")), flush=True)
        if not resources_before.get("ok", False):
            raise RuntimeError(f"resource guard not OK before run: {resources_before}")

        session = api.post("/api/session/reset", {"start_position_m": [0.0, 0.0, 0.0]})
        simulation = session.get("simulation") or {}
        container_name = simulation.get("container_name")
        print("CP0_SINGLE_SESSION_RESET=" + json.dumps(session, separators=(",", ":")), flush=True)

        payload = build_payload()
        queued = api.post("/api/experiment/start", payload)
        run_id = str(queued["run_id"])
        print(f"CP0_SINGLE_RUN_ID={run_id}", flush=True)

        terminal = wait_for_run(api, run_id, args.run_wait_s)
        print("CP0_SINGLE_TERMINAL=" + json.dumps(terminal, separators=(",", ":")), flush=True)
        errors.extend(validate_terminal(terminal))

        session_after = api.get("/api/session")
        resources_after = api.get("/api/resources")
        print("CP0_SINGLE_SESSION_AFTER=" + json.dumps(session_after, separators=(",", ":")), flush=True)
        print("CP0_SINGLE_RESOURCE_AFTER=" + json.dumps(resources_after, separators=(",", ":")), flush=True)
        if not resources_after.get("ok", False):
            errors.append("resource_guard_not_ok_after_run")
        if resources_after.get("mavsdk_server_process_count") not in (0, None):
            errors.append(f"mavsdk_server_count_after_run:{resources_after.get('mavsdk_server_process_count')}")
    except Exception as exc:
        errors.append(f"acceptance_exception:{type(exc).__name__}:{exc}")
    finally:
        server_stop(process)
        cleanup_ok, cleanup_errors = wait_for_cleanup(container_name, args.cleanup_timeout_s)
        if not cleanup_ok:
            errors.extend(cleanup_errors)

    if errors:
        print("CP0_SINGLE_FLIGHT=FAIL", flush=True)
        for item in errors:
            print(f"CP0_SINGLE_ERROR={item}", flush=True)
        return 2

    print("CP0_SINGLE_FLIGHT=PASS", flush=True)
    print("CP0_SINGLE_CLEANUP=PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
