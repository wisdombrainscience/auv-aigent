from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ResourceLimitError(RuntimeError):
    """Raised when a new experiment would exceed a configured resource guard."""


@dataclass(frozen=True)
class ResourcePolicy:
    version: int = 1
    max_in_memory_runs: int = 64
    max_timeout_s: float = 300.0
    max_runs_size_mb: float = 5120.0
    min_free_disk_mb: float = 1024.0
    live_buffer_points: int = 300
    telemetry_rate_hz: float = 10.0
    flush_every_samples: int = 5
    history_page_size: int = 50
    storage_status_cache_s: float = 5.0
    telemetry_batch_limit: int = 120
    single_flight_poll_ms: int = 750
    mission_poll_ms: int = 500
    resource_poll_s: float = 15.0
    json_gzip_min_bytes: int = 2048
    retention_pass_days: int = 30
    retention_fail_days: int = 90
    retention_incomplete_days: int = 7
    retention_keep_latest_per_scenario: int = 20


def _positive_number(raw: dict[str, Any], key: str, default: float) -> float:
    value = float(raw.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be > 0")
    return value


def load_resource_policy(path: Path) -> ResourcePolicy:
    path = Path(path)
    if not path.is_file():
        return ResourcePolicy()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("resource policy must be a mapping")
    if int(raw.get("version", 1)) != 1:
        raise ValueError("unsupported resource policy version")

    memory = raw.get("memory", {}) or {}
    storage = raw.get("storage", {}) or {}
    telemetry = raw.get("telemetry", {}) or {}
    ui = raw.get("ui", {}) or {}
    runtime = raw.get("runtime", {}) or {}
    network = raw.get("network", {}) or {}
    retention = raw.get("retention", {}) or {}

    return ResourcePolicy(
        version=1,
        max_in_memory_runs=int(_positive_number(memory, "max_in_memory_runs", 64)),
        max_timeout_s=_positive_number(raw, "max_timeout_s", 300.0),
        max_runs_size_mb=_positive_number(storage, "max_runs_size_mb", 5120.0),
        min_free_disk_mb=_positive_number(storage, "min_free_disk_mb", 1024.0),
        live_buffer_points=int(_positive_number(telemetry, "live_buffer_points", 300)),
        telemetry_rate_hz=_positive_number(telemetry, "rate_hz", 10.0),
        flush_every_samples=int(_positive_number(telemetry, "flush_every_samples", 5)),
        history_page_size=int(_positive_number(ui, "history_page_size", 50)),
        storage_status_cache_s=_positive_number(runtime, "storage_status_cache_s", 5.0),
        telemetry_batch_limit=int(_positive_number(network, "telemetry_batch_limit", 120)),
        single_flight_poll_ms=int(_positive_number(network, "single_flight_poll_ms", 750)),
        mission_poll_ms=int(_positive_number(network, "mission_poll_ms", 500)),
        resource_poll_s=_positive_number(network, "resource_poll_s", 15.0),
        json_gzip_min_bytes=int(_positive_number(network, "json_gzip_min_bytes", 2048)),
        retention_pass_days=int(_positive_number(retention, "pass_days", 30)),
        retention_fail_days=int(_positive_number(retention, "fail_days", 90)),
        retention_incomplete_days=int(_positive_number(retention, "incomplete_days", 7)),
        retention_keep_latest_per_scenario=int(
            _positive_number(retention, "keep_latest_per_scenario", 20)
        ),
    )


class ResourceManager:
    """Bounded resource guard for long-running localhost experiment sessions.

    It never auto-deletes evidence. It reports pressure and blocks a new run
    before the filesystem reaches a dangerous state. Retention/archival can be
    added on top without weakening evidence integrity.
    """

    def __init__(self, runs_root: Path, policy: ResourcePolicy | None = None) -> None:
        self.runs_root = Path(runs_root)
        self.policy = policy or ResourcePolicy()
        self._size_cache_lock = threading.Lock()
        self._size_cache_bytes: int | None = None
        self._size_cache_at = 0.0

    @staticmethod
    def _tree_size(path: Path) -> int:
        total = 0
        if not path.exists():
            return total
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _process_rss_mb() -> float | None:
        """Return current process RSS on Linux without optional dependencies."""
        status_path = Path("/proc/self/status")
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    # Linux reports VmRSS in KiB.
                    return round(float(line.split()[1]) / 1024.0, 3)
        except (OSError, ValueError, IndexError):
            return None
        return None

    @staticmethod
    def _open_fd_count() -> int | None:
        try:
            return len(list(Path("/proc/self/fd").iterdir()))
        except OSError:
            return None

    @staticmethod
    def _child_process_counts() -> tuple[int | None, int | None]:
        """Return direct child count and embedded mavsdk_server count on Linux."""
        proc = Path("/proc")
        if not proc.is_dir():
            return None, None
        parent = os.getpid()
        children = 0
        mavsdk_servers = 0
        try:
            entries = list(proc.iterdir())
        except OSError:
            return None, None
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / "status").read_text(encoding="utf-8", errors="replace")
                ppid = None
                for line in status.splitlines():
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
                if ppid != parent:
                    continue
                children += 1
                cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
                if "mavsdk_server" in cmdline:
                    mavsdk_servers += 1
            except (OSError, ValueError, IndexError):
                continue
        return children, mavsdk_servers

    def _runs_size_cached(self) -> int:
        """Return runs tree size with a short TTL to avoid repeated full scans.

        The web UI polls resource status periodically. Walking thousands of
        evidence files on every poll causes unnecessary disk I/O and latency.
        A few seconds of staleness is safe because the hard free-space guard is
        also checked before every new execution.
        """
        now = time.monotonic()
        with self._size_cache_lock:
            if (
                self._size_cache_bytes is not None
                and now - self._size_cache_at < self.policy.storage_status_cache_s
            ):
                return self._size_cache_bytes
        measured = self._tree_size(self.runs_root)
        with self._size_cache_lock:
            self._size_cache_bytes = measured
            self._size_cache_at = now
        return measured

    def invalidate_storage_cache(self) -> None:
        with self._size_cache_lock:
            self._size_cache_bytes = None
            self._size_cache_at = 0.0

    def status(self) -> dict[str, Any]:
        probe = self.runs_root if self.runs_root.exists() else self.runs_root.parent
        probe.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(probe)
        runs_bytes = self._runs_size_cached()
        free_mb = usage.free / (1024 * 1024)
        runs_mb = runs_bytes / (1024 * 1024)
        quota_mb = self.policy.max_runs_size_mb
        ok = free_mb >= self.policy.min_free_disk_mb and runs_mb < quota_mb
        child_count, mavsdk_server_count = self._child_process_counts()
        return {
            "ok": ok,
            "runs_bytes": runs_bytes,
            "runs_mb": round(runs_mb, 3),
            "max_runs_size_mb": quota_mb,
            "free_disk_mb": round(free_mb, 1),
            "min_free_disk_mb": self.policy.min_free_disk_mb,
            "usage_percent_of_runs_quota": round((runs_mb / quota_mb) * 100.0, 2),
            "max_timeout_s": self.policy.max_timeout_s,
            "live_buffer_points": self.policy.live_buffer_points,
            "telemetry_rate_hz": self.policy.telemetry_rate_hz,
            "process_rss_mb": self._process_rss_mb(),
            "process_threads": len(os.listdir("/proc/self/task")) if Path("/proc/self/task").is_dir() else None,
            "open_fd_count": self._open_fd_count(),
            "child_process_count": child_count,
            "mavsdk_server_process_count": mavsdk_server_count,
        }

    def assert_can_start(self, timeout_s: float) -> None:
        if float(timeout_s) > self.policy.max_timeout_s:
            raise ResourceLimitError(
                f"timeout_s exceeds configured maximum ({self.policy.max_timeout_s:g}s)"
            )
        # New executions get an exact quota measurement; only UI status polls
        # use the short-lived disk-size cache.
        self.invalidate_storage_cache()
        status = self.status()
        if status["free_disk_mb"] < self.policy.min_free_disk_mb:
            raise ResourceLimitError(
                "insufficient free disk space for a new evidence run: "
                f"{status['free_disk_mb']:.1f} MB free, "
                f"minimum {self.policy.min_free_disk_mb:.1f} MB"
            )
        if status["runs_mb"] >= self.policy.max_runs_size_mb:
            raise ResourceLimitError(
                "runs storage quota reached: "
                f"{status['runs_mb']:.1f} MB / {self.policy.max_runs_size_mb:.1f} MB"
            )
