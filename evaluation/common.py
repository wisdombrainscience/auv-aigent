from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

TERMINAL_STATUSES = {"PASS", "FAIL", "FAILED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def percentile(values: Iterable[float], q: float) -> float | None:
    xs = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    q = min(1.0, max(0.0, float(q)))
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def wilson_ci(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = successes / n
    zz = z * z
    denom = 1.0 + zz / n
    center = (p + zz / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / n) + (zz / (4.0 * n * n))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def describe(values: Iterable[Any]) -> dict[str, float | int | None]:
    xs = [x for x in (finite(v) for v in values) if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "sd": None, "median": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(xs),
        "mean": statistics.fmean(xs),
        "sd": statistics.stdev(xs) if len(xs) > 1 else 0.0,
        "median": statistics.median(xs),
        "p95": percentile(xs, 0.95),
        "min": min(xs),
        "max": max(xs),
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cmd(args: list[str]) -> str | None:
    try:
        return subprocess.check_output(args, cwd=PROJECT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def source_manifest() -> dict[str, Any]:
    critical = [
        "pyproject.toml",
        "orchestration/capability_experiment.py",
        "orchestration/mission_experiment.py",
        "orchestration/web_experiment.py",
        "src/adapters/px4_sitl_adapter.py",
        "src/verification/takeoff_verifier.py",
        "src/verification/goto_target_verifier.py",
        "src/verification/return_home_verifier.py",
        "src/verification/land_verifier.py",
        "config/resource_policy.yaml",
        "specs/TAKEOFF-001.yaml",
        "specs/NAV-001.yaml",
        "specs/RETURN-HOME-001.yaml",
        "specs/LAND-001.yaml",
    ]
    hashes: dict[str, str] = {}
    for rel in critical:
        p = PROJECT / rel
        if p.is_file():
            hashes[rel] = sha256_file(p)
    status = _cmd(["git", "status", "--short"])
    return {
        "captured_at": utc_now(),
        "project": str(PROJECT),
        "git_branch": _cmd(["git", "branch", "--show-current"]),
        "git_head": _cmd(["git", "rev-parse", "HEAD"]),
        "git_status_short": status.splitlines() if status else [],
        "working_tree_dirty": bool(status),
        "critical_file_sha256": hashes,
        "python": sys.version,
        "platform": platform.platform(),
        "hostname": platform.node(),
    }


def environment_manifest() -> dict[str, Any]:
    return {
        "captured_at": utc_now(),
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "docker_version": _cmd(["docker", "--version"]),
        "uname": _cmd(["uname", "-a"]),
        "lsb_release": _cmd(["lsb_release", "-ds"]),
        "cpu_model": _cmd(["bash", "-lc", "lscpu | grep -m1 'Model name' | sed 's/^[^:]*:[[:space:]]*//'" ]),
        "memory": _cmd(["bash", "-lc", "free -h | sed -n '2p'" ]),
    }


class LocalApi:
    def __init__(self, base_url: str = "http://127.0.0.1:8370", timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.latencies_ms: list[float] = []

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], float]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        t0 = time.perf_counter()
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {text}") from exc
        except URLError as exc:
            raise RuntimeError(f"cannot reach {self.base_url}: {exc}") from exc
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.latencies_ms.append(elapsed_ms)
        return body, elapsed_ms

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)[0]

    def get_timed(self, path: str) -> tuple[dict[str, Any], float]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)[0]

    def post_timed(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        return self._request("POST", path, payload)


def wait_for_run(api: LocalApi, run_id: str, timeout_s: float = 60.0, poll_s: float = 0.4) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.get(f"/api/experiment/{run_id}/status")
        if str(last.get("status", "")).upper() in TERMINAL_STATUSES:
            return last
        time.sleep(poll_s)
    raise TimeoutError(f"run {run_id} did not finish within {timeout_s:g}s; last={last}")


def wait_for_session_commit(api: LocalApi, run_id: str, timeout_s: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.get("/api/session")
        if last.get("last_run_id") == run_id:
            return last
        time.sleep(0.1)
    return last


def classify_module_verifier(module_status: str | None, verifier_overall: str | None) -> str:
    module_ok = str(module_status or "").lower() == "completed"
    verifier_ok = str(verifier_overall or "").upper() == "PASS"
    if module_ok and verifier_ok:
        return "AGREE_PASS"
    if (not module_ok) and (not verifier_ok):
        return "AGREE_FAIL"
    if module_ok and (not verifier_ok):
        return "SILENT_FAIL"
    return "V_ONLY_LENIENT"


def extract_attempt_row(attempt_dir: Path, capability_id: str, condition: str | None = None) -> dict[str, Any]:
    attempt_dir = Path(attempt_dir)
    module = read_json(attempt_dir / "module_output.json", {}) or {}
    verification = read_json(attempt_dir / "verification.json", {}) or {}
    metadata = read_json(attempt_dir / "metadata.json", {}) or {}
    input_data = read_json(attempt_dir / "input.json", {}) or {}
    module_status = module.get("status")
    verifier = verification.get("overall")
    row = {
        "capability": capability_id,
        "condition": condition,
        "run_id": metadata.get("run_id"),
        "attempt_dir": str(attempt_dir),
        "module_status": module_status,
        "module_error_code": module.get("error_code"),
        "module_reason": module.get("reason"),
        "verifier_overall": verifier,
        "verifier_reason": verification.get("reason"),
        "classification": classify_module_verifier(module_status, verifier),
        "position_error_m": verification.get("position_error_m"),
        "altitude_error_m": verification.get("altitude_error_m"),
        "horizontal_error_m": verification.get("horizontal_error_m"),
        "horizontal_drift_m": verification.get("horizontal_drift_m"),
        "final_altitude_m": verification.get("final_altitude_m"),
        "elapsed_time_s": verification.get("elapsed_time_s"),
        "checks": verification.get("checks"),
        "input": input_data,
    }
    return row
