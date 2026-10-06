from __future__ import annotations

import json
import threading
from src.runtime_paths import runs_root
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionError(RuntimeError):
    pass


class Step37ASession:
    """Persistent localhost-only session state for STEP37A simulation."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self.path = runs_root(self.project) / "NAV-001" / "step37a_session.json"
        self._lock = threading.Lock()
        self._state = self._load()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._new_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("scope") == "SIMULATION_ONLY":
                if "current_vehicle_position_m" not in data:
                    data["current_vehicle_position_m"] = data.get(
                        "current_requested_start_position_m"
                    )
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return self._new_state()

    def _new_state(self) -> dict[str, Any]:
        return {
            "scope": "SIMULATION_ONLY",
            "session_id": None,
            "state": "NEW",
            "mode": None,
            "last_run_id": None,
            "last_verified_final_position_m": None,
            "home_position_m": None,
            "current_requested_start_position_m": None,
            "current_vehicle_position_m": None,
            "current_target_position_m": None,
            "simulation": {
                "managed": False,
                "container_name": None,
                "spawn_position_ned_m": None,
            },
            "updated_at": self._now(),
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = self._now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def reset(self, start_position_m: list[float], simulation: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            session_id = datetime.now(timezone.utc).strftime("S37A-%Y%m%dT%H%M%S-%fZ")
            self._state = {
                "scope": "SIMULATION_ONLY",
                "session_id": session_id,
                "state": "READY",
                "mode": "NEW_SESSION",
                "last_run_id": None,
                "last_verified_final_position_m": None,
                "home_position_m": list(start_position_m),
                "current_requested_start_position_m": list(start_position_m),
                "current_vehicle_position_m": list(start_position_m),
                "current_target_position_m": None,
                "simulation": dict(simulation),
                "updated_at": self._now(),
            }
            self._save()
            return json.loads(json.dumps(self._state))

    def prepare_continue(self, target_position_m: list[float]) -> dict[str, Any]:
        with self._lock:
            if not self._state.get("session_id"):
                raise SessionError("no active simulation session; use Reset/New Session first")
            final = self._state.get("last_verified_final_position_m")
            if not isinstance(final, list) or len(final) != 3:
                raise SessionError("continue is unavailable until a previous run has a verified final position")
            self._state["state"] = "READY"
            self._state["mode"] = "CONTINUE"
            self._state["current_requested_start_position_m"] = list(final)
            self._state["current_target_position_m"] = list(target_position_m)
            self._save()
            return json.loads(json.dumps(self._state))

    def mark_run_started(self, run_id: str, target_position_m: list[float]) -> None:
        with self._lock:
            self._state["state"] = "RUNNING"
            self._state["last_run_id"] = run_id
            self._state["current_target_position_m"] = list(target_position_m)
            self._save()

    def sync_measured_start(self, run_id: str, position_m: list[float]) -> None:
        """Update the active run's start anchor from fresh vehicle telemetry.

        ``last_verified_final_position_m`` remains historical evidence from the
        previous run.  The measured start is the authoritative start for the
        current run and may differ slightly while the vehicle settles/holds.
        """
        with self._lock:
            if self._state.get("last_run_id") != run_id:
                raise SessionError("cannot sync start for a non-active run")
            self._state["current_requested_start_position_m"] = list(position_m)
            self._state["current_vehicle_position_m"] = list(position_m)
            self._save()

    def mark_run_completed(
        self,
        run_id: str,
        final_position_m: list[float] | None,
        verdict: str,
        current_position_m: list[float] | None = None,
    ) -> None:
        """Finalize one interactive NAV run without losing live vehicle state.

        ``final_position_m`` is the verifier/evidence terminal position and must
        remain the historical continuity anchor.  ``current_position_m`` may be
        a fresher post-Hold telemetry sample taken after verification.  Keeping
        these separate prevents the browser/session from pretending that the
        vehicle is still exactly at the frozen verifier sample between runs.
        """
        with self._lock:
            self._state["last_run_id"] = run_id
            if final_position_m is not None:
                self._state["last_verified_final_position_m"] = list(final_position_m)
                self._state["current_requested_start_position_m"] = list(final_position_m)
                self._state["current_vehicle_position_m"] = list(final_position_m)
            if current_position_m is not None:
                if len(current_position_m) != 3:
                    raise SessionError("current position must contain 3 coordinates")
                self._state["current_vehicle_position_m"] = [
                    float(v) for v in current_position_m
                ]
            self._state["state"] = "COMPLETED"
            self._state["mode"] = self._state.get("mode") or "NEW_SESSION"
            self._state["last_verdict"] = verdict
            self._save()


    def mark_landed(self, position_m: list[float] | None = None) -> None:
        """Mark the session landed and refresh the displayed vehicle position.

        ``current_requested_start_position_m`` intentionally remains the last
        airborne navigation anchor so simulation auto-takeoff can prepare back
        to that altitude for a later NAV-001 run.  ``current_vehicle_position_m``
        is the live/display state and may be at ground level after landing.
        """
        with self._lock:
            if not self._state.get("session_id"):
                raise SessionError("no active simulation session")
            if position_m is not None:
                if len(position_m) != 3:
                    raise SessionError("landed position must contain 3 coordinates")
                self._state["current_vehicle_position_m"] = [float(v) for v in position_m]
            self._state["state"] = "LANDED"
            self._state["mode"] = "LANDED"
            self._save()

    def mark_reset_required(self) -> None:
        with self._lock:
            if self._state.get("session_id"):
                self._state["state"] = "RESET_REQUIRED"
                self._state["mode"] = "RESET_REQUIRED"
                self._save()
