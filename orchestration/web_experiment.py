from __future__ import annotations

import asyncio
import csv
import json
import math
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.runtime_paths import runs_root
from orchestration.runtime_input_bridge import prepare_runtime_attempt
from src.capabilities.catalog import build_default_registry
from src.core.executor import CapabilityExecutor
from src.evidence.attempt import AttemptEvidence
from src.evidence.contract import EvidenceContractValidator
from src.evidence.index import EvidenceIndex
from src.telemetry.recorder import TelemetrySample
from src.resources.manager import ResourceManager, load_resource_policy
from user_interface.input_schema import UserRuntimeInput
from orchestration.step37a_session import SessionError, Step37ASession
from orchestration.step37a_simulation import SimulationSupervisor


class ExperimentStore:
    """Thread-safe bounded registry for active/recent localhost jobs.

    Historical metadata belongs in SQLite; keeping this cache bounded prevents
    a long-running web console from growing RAM with every experiment.
    """

    def __init__(self, max_items: int = 64) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be > 0")
        self.max_items = int(max_items)
        self._lock = threading.Lock()
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _trim(self) -> None:
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def create(self, run_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._items[run_id] = dict(payload)
            self._items.move_to_end(run_id)
            self._trim()

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            if run_id in self._items:
                self._items[run_id].update(fields)
                self._items.move_to_end(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(run_id)
            return dict(item) if item else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class Step37AExperimentManager:
    """Runs NAV-001 against an already-started PX4 SITL instance.

    The web layer never supplies telemetry truth and never decides PASS/FAIL.
    The independent NAV-001 verifier remains authoritative.
    """

    def __init__(
        self,
        project: Path,
        store: ExperimentStore | None = None,
        simulation: SimulationSupervisor | None = None,
    ) -> None:
        self.project = Path(project).resolve()
        self.runs_root = runs_root(self.project)
        self.policy_path = self.project / "config" / "input_policy.yaml"
        self.capability_id = "NAV-001"
        self.registry = build_default_registry()
        self.definition = self.registry.get_definition(self.capability_id)
        self.executor = CapabilityExecutor(self.registry, self.project)
        self.spec_path = self.definition.spec_path(self.project)
        self.resource_policy_path = self.project / "config" / "resource_policy.yaml"
        self.resource_policy = load_resource_policy(self.resource_policy_path)
        self.store = store if store is not None else ExperimentStore(self.resource_policy.max_in_memory_runs)
        self.resources = ResourceManager(self.runs_root, self.resource_policy)
        self.index = EvidenceIndex(self.runs_root / "index.sqlite3")
        self.index.backfill_from_runs(self.runs_root / self.capability_id)
        self.contract = (
            EvidenceContractValidator(self.spec_path)
            if self.spec_path.is_file()
            else None
        )
        self._active_lock = threading.Lock()
        self._active_run: str | None = None
        self._telemetry_lock = threading.Lock()
        self._live_telemetry: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self.session = Step37ASession(self.project)
        self.simulation = simulation if simulation is not None else SimulationSupervisor(self.project)

    def session_status(self) -> dict[str, Any]:
        state = self.session.snapshot()
        if state.get("session_id"):
            simulation = dict(state.get("simulation") or {})
            live = self.simulation.live_status()
            simulation.update(live)
            state["simulation"] = simulation
            if live.get("status") != "RUNNING" and state.get("state") not in {"NEW", "RESET_REQUIRED"}:
                state["state"] = "RESET_REQUIRED"
                state["mode"] = "RESET_REQUIRED"
        return state

    def has_active_run(self) -> bool:
        with self._active_lock:
            if self._active_run is None:
                return False
            current = self.store.get(self._active_run)
            return bool(current and current.get("status") in {"QUEUED", "RUNNING"})

    def resource_status(self) -> dict[str, Any]:
        return self.resources.status()

    def public_config(self) -> dict[str, Any]:
        policy = __import__("user_interface.policy", fromlist=["load_input_policy"]).load_input_policy(self.policy_path)
        area = policy.test_area.to_dict()
        return {
            "frame": "LOCAL_NED",
            "workspace": area,
            "ui": {
                "north_min_m": area["x_min"],
                "north_max_m": area["x_max"],
                "east_min_m": area["y_min"],
                "east_max_m": area["y_max"],
                "altitude_min_m": max(0.0, -area["z_max"]),
                "altitude_max_m": max(0.0, -area["z_min"]),
            },
            "max_timeout_s": self.resource_policy.max_timeout_s,
            "telemetry_rate_hz": self.resource_policy.telemetry_rate_hz,
            "live_buffer_points": self.resource_policy.live_buffer_points,
            "telemetry_batch_limit": self.resource_policy.telemetry_batch_limit,
            "single_flight_poll_ms": self.resource_policy.single_flight_poll_ms,
            "mission_poll_ms": self.resource_policy.mission_poll_ms,
            "resource_poll_s": self.resource_policy.resource_poll_s,
            "capabilities": self.registry.describe(),
        }

    def history(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return {
            "total": self.index.count(),
            "limit": limit,
            "offset": offset,
            "items": self.index.list_recent(limit=limit, offset=offset),
        }

    def latest_telemetry_for_run(
        self,
        run_id: str,
        *,
        since_timestamp: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return bounded telemetry, optionally only samples newer than a cursor.

        Legacy callers that omit ``since_timestamp`` still receive the current
        tail. The web console uses the cursor form so each sample is transferred
        once instead of re-sending the whole trajectory on every poll.
        """
        cap = self.resource_policy.telemetry_batch_limit if limit is None else max(1, min(int(limit), self.resource_policy.live_buffer_points))
        with self._telemetry_lock:
            buffer = self._live_telemetry.get(run_id)
            if buffer is not None:
                self._live_telemetry.move_to_end(run_id)
                rows = list(buffer)
            else:
                rows = []
        if not rows:
            item = self.store.get(run_id)
            if not item or not item.get("attempt_dir"):
                return []
            rows = latest_telemetry(
                Path(item["attempt_dir"]),
                self.resource_policy.live_buffer_points,
            )
        if since_timestamp is not None:
            filtered: list[dict[str, Any]] = []
            for row in rows:
                try:
                    ts = float(row.get("timestamp", row.get("timestamp_s")))
                except (TypeError, ValueError):
                    continue
                if ts > since_timestamp:
                    filtered.append(row)
            rows = filtered
        return rows[-cap:]

    def _push_live_telemetry(self, run_id: str, row: dict[str, Any]) -> None:
        with self._telemetry_lock:
            buffer = self._live_telemetry.get(run_id)
            if buffer is None:
                buffer = deque(maxlen=self.resource_policy.live_buffer_points)
                self._live_telemetry[run_id] = buffer
            buffer.append(dict(row))
            self._live_telemetry.move_to_end(run_id)
            while len(self._live_telemetry) > self.resource_policy.max_in_memory_runs:
                self._live_telemetry.popitem(last=False)

    def land_session(self) -> dict[str, Any]:
        with self._active_lock:
            current = self.store.get(self._active_run) if self._active_run else None
            if current and current.get("status") in {"QUEUED", "RUNNING"}:
                raise RuntimeError("cannot land while a capability is running")
        return asyncio.run(self._land_session_async())

    async def _land_session_async(self) -> dict[str, Any]:
        from src.adapters.px4_sitl_adapter import PX4SITLAdapter

        adapter = PX4SITLAdapter()
        await adapter.connect()
        try:
            state = await adapter.get_state()
            if state.airborne:
                await adapter.land_and_wait(timeout_s=30.0)
            await adapter.abort()
            landed = await adapter.get_position()
            landed_position = [landed.x, landed.y, landed.z]
            self.session.mark_landed(landed_position)
            return {
                "status": "LANDED",
                "landed_position_m": landed_position,
                "session": self.session.snapshot(),
            }
        finally:
            try:
                await adapter.abort()
            except Exception:
                pass
            await self._close_adapter(adapter)

    @staticmethod
    async def _close_adapter(adapter: Any) -> str | None:
        close = getattr(adapter, "close", None)
        if close is None:
            return None
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _ensure_telemetry_artifact(path: Path) -> None:
        """Create a header-only telemetry artifact before runtime setup.

        Connection/preflight failures happen before the recorder task starts.
        The frozen evidence contract still requires telemetry.csv, so creating
        the schema up front keeps error attempts finalizable without inventing
        samples or masking the original runtime error.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        fields = [
            "timestamp", "x", "y", "z", "vx", "vy", "vz",
            "vehicle_state", "localization_valid",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()

    @staticmethod
    def _write_changed_files_marker(evidence: AttemptEvidence) -> None:
        evidence.write_json(
            "changed_files.json",
            {
                "changed_files": [],
                "reason": "runtime_experiment_no_code_mutation",
            },
        )

    def _validate_and_finalize(self, evidence: AttemptEvidence) -> None:
        if self.contract is None:
            raise RuntimeError(f"evidence contract spec is unavailable: {self.spec_path}")
        result = self.contract.validate(evidence.path)
        evidence.write_json("evidence_contract_result.json", result.to_dict())
        if not result.complete:
            raise RuntimeError(
                "evidence contract incomplete: " + ",".join(result.missing)
            )
        evidence.finalize()

    def _index_completed_run(
        self,
        run_id: str,
        *,
        attempt: Path,
        canonical: dict[str, Any],
        verification: dict[str, Any],
        final_position: list[float] | None,
        status: str,
        created_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        self.index.upsert(
            {
                "run_id": run_id,
                "task_id": "NAV-001",
                "scenario_id": canonical.get("scenario_id"),
                "attempt_dir": str(attempt),
                "status": status,
                "verdict": verification.get("overall"),
                "position_error_m": verification.get("position_error_m"),
                "elapsed_time_s": verification.get("elapsed_time_s"),
                "target_position_m": canonical.get("target_position_m"),
                "final_position_m": final_position,
                "created_at": created_at,
                "finished_at": finished_at,
            }
        )

    def reset_session(self, start_position_m: list[float]) -> dict[str, Any]:
        canonical_start = [self._finite(v, "start_position_m") for v in start_position_m]
        if len(canonical_start) != 3:
            raise ValueError("start_position_m must be a 3-element array")
        policy = __import__("user_interface.policy", fromlist=["load_input_policy"]).load_input_policy(self.policy_path)
        bounds = policy.test_area
        if bounds is None or not bounds.contains(*canonical_start):
            raise ValueError("start_position_m is outside permitted simulation region")
        with self._active_lock:
            current = self.store.get(self._active_run) if self._active_run else None
            if current and current.get("status") in {"QUEUED", "RUNNING"}:
                raise RuntimeError("cannot reset while an experiment is running")
        simulation = self.simulation.start_new(canonical_start)
        state = self.session.reset(canonical_start, simulation)
        return state

    def continue_session(self, target_position_m: list[float]) -> dict[str, Any]:
        target = [self._finite(v, "target_position_m") for v in target_position_m]
        if len(target) != 3:
            raise ValueError("target_position_m must be a 3-element array")
        policy = __import__("user_interface.policy", fromlist=["load_input_policy"]).load_input_policy(self.policy_path)
        if policy.test_area is None or not policy.test_area.contains(*target):
            raise ValueError("target_position_m is outside permitted simulation region")
        return self.session.prepare_continue(target)

    def start(self, request: dict[str, Any]) -> str:
        self.resources.assert_can_start(float(request.get("timeout_s", 0)))
        session = self.session.snapshot()
        if session.get("state") == "NEW" or not session.get("session_id"):
            raise RuntimeError("no active simulation session; use Reset/New Session first")
        if session.get("state") == "RUNNING":
            raise RuntimeError("an experiment is already running")
        if session.get("state") == "RESET_REQUIRED":
            raise RuntimeError("simulation reset is required before another experiment")
        if not self.simulation.is_running():
            self.session.mark_reset_required()
            raise RuntimeError("simulation container is not running; use Start / Restart Simulation")

        # A successful completed run automatically becomes CONTINUE when the
        # operator selects the next target.  Earlier versions relied on a
        # separate /api/session/continue call that the Single Flight browser did
        # not issue, so the second flight stayed in NEW_SESSION mode and could
        # fail the hard start gate after normal Hold drift.
        if session.get("state") == "COMPLETED":
            if session.get("last_verdict") != "PASS":
                self.session.mark_reset_required()
                raise RuntimeError(
                    "previous single-flight run did not PASS; restart simulation before continuing"
                )
            target = request.get("target_position_m")
            if not isinstance(target, list) or len(target) != 3:
                raise ValueError("target_position_m must be a 3-element array")
            session = self.continue_session(target)

        # The server owns the authoritative start position.  For an airborne
        # CONTINUE run, preserve the previous verified terminal position as the
        # audit/continuity anchor; fresh MAVSDK telemetry will become the actual
        # execution start at START_GATE.  After Land, however, the true vehicle
        # position is on the ground and must drive the auto-takeoff bootstrap.
        if session.get("state") == "LANDED" or session.get("mode") == "LANDED":
            session_start = session.get("current_vehicle_position_m")
        else:
            session_start = session.get("current_requested_start_position_m")
        if not isinstance(session_start, list) or len(session_start) != 3:
            session_start = session.get("current_vehicle_position_m")
        if not isinstance(session_start, list) or len(session_start) != 3:
            self.session.mark_reset_required()
            raise RuntimeError("active session has no valid start position; restart simulation")
        request = dict(request)
        request["start_position_m"] = [float(v) for v in session_start]
        with self._active_lock:
            if self._active_run is not None:
                current = self.store.get(self._active_run)
                if current and current.get("status") in {"QUEUED", "RUNNING"}:
                    raise RuntimeError("an experiment is already running")
                self._active_run = None

            run_id = datetime.now(timezone.utc).strftime("NAV001-%Y%m%dT%H%M%S-%fZ")
            self.store.create(
                run_id,
                {
                    "run_id": run_id,
                    "status": "QUEUED",
                    "phase": "INPUT_VALIDATION",
                    "scope": "SIMULATION_ONLY",
                    "verdict_authority": "independent_python_verifier",
                    "ai_runtime_control": False,
                    "request": request,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self._active_run = run_id
            with self._telemetry_lock:
                self._live_telemetry[run_id] = deque(
                    maxlen=self.resource_policy.live_buffer_points
                )
                self._live_telemetry.move_to_end(run_id)
                while len(self._live_telemetry) > self.resource_policy.max_in_memory_runs:
                    self._live_telemetry.popitem(last=False)
            self.session.mark_run_started(run_id, request["target_position_m"])

        thread = threading.Thread(
            target=self._thread_entry,
            args=(run_id, request),
            name=f"{run_id}-worker",
            daemon=True,
        )
        thread.start()
        return run_id

    def _thread_entry(self, run_id: str, request: dict[str, Any]) -> None:
        try:
            asyncio.run(self._run(run_id, request))
        except Exception as exc:  # last-resort job containment
            try:
                self.session.mark_reset_required()
            except Exception:
                pass
            self.store.update(
                run_id,
                status="FAILED",
                phase="ERROR",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            with self._active_lock:
                if self._active_run == run_id:
                    self._active_run = None

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        out = float(value)
        if not math.isfinite(out):
            raise ValueError(f"{name} must be finite")
        return out

    @classmethod
    def validate_request(cls, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("request must be a JSON object")

        def vector(name: str) -> list[float]:
            value = raw.get(name)
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError(f"{name} must be a 3-element array")
            return [cls._finite(v, name) for v in value]

        start = vector("start_position_m")
        target = vector("target_position_m")
        tolerance = cls._finite(raw.get("position_tolerance_m"), "position_tolerance_m")
        start_tolerance = cls._finite(raw.get("start_position_tolerance_m", 0.75), "start_position_tolerance_m")
        timeout = cls._finite(raw.get("timeout_s"), "timeout_s")
        if tolerance <= 0 or start_tolerance <= 0 or timeout <= 0:
            raise ValueError("tolerances and timeout must be > 0")
        # The target must always be airborne for NAV-001.  The session start,
        # however, may be exactly ground level (z == 0) when Gazebo is spawned
        # realistically on the ground.  In that case Single Flight performs a
        # simulation-only auto-takeoff bootstrap *before* opening the NAV-001
        # evidence window.  Positive Down (z > 0) is still rejected.
        if start[2] > 0:
            raise ValueError("start Z must be <= 0 in LOCAL_NED")
        if target[2] >= 0:
            raise ValueError("target Z must be < 0 in LOCAL_NED for NAV-001")

        frame = raw.get("frame", "LOCAL_NED")
        if frame != "LOCAL_NED":
            raise ValueError("only LOCAL_NED is supported")

        scenario_id = str(raw.get("scenario_id") or "STEP37A-DEMO").strip()
        if not scenario_id or len(scenario_id) > 80:
            raise ValueError("scenario_id must be 1..80 characters")

        return {
            "start_position_m": start,
            "target_position_m": target,
            "position_tolerance_m": tolerance,
            "start_position_tolerance_m": start_tolerance,
            "timeout_s": timeout,
            "frame": frame,
            "scenario_id": scenario_id,
        }

    async def _run(self, run_id: str, request: dict[str, Any]) -> None:
        canonical = self.validate_request(request)
        policy = __import__("user_interface.policy", fromlist=["load_input_policy"]).load_input_policy(self.policy_path)
        bounds = policy.test_area
        if bounds is None:
            raise ValueError("simulation region is not configured")
        if not bounds.contains(*canonical["start_position_m"]):
            raise ValueError("start_position_m is outside permitted simulation region")
        if not bounds.contains(*canonical["target_position_m"]):
            raise ValueError("target_position_m is outside permitted simulation region")
        session_mode = self.session.snapshot().get("mode")
        expected_start_position = list(canonical["start_position_m"])
        user_input = UserRuntimeInput(
            target_position_m=tuple(canonical["target_position_m"]),
            position_tolerance_m=canonical["position_tolerance_m"],
            timeout_s=canonical["timeout_s"],
            frame=canonical["frame"],
            scenario_id=canonical["scenario_id"],
        )

        self.store.update(run_id, status="RUNNING", phase="PREPARE_ATTEMPT")
        prepared = prepare_runtime_attempt(
            runtime_input=user_input,
            policy_path=self.policy_path,
            runs_root=self.runs_root,
            task_id=self.capability_id,
        )
        attempt = prepared.attempt_dir
        self.store.update(run_id, attempt_dir=str(attempt), phase="CONNECT_PX4_SITL")

        evidence = AttemptEvidence(
            root=self.runs_root,
            task_id=self.capability_id,
            scenario_id=canonical["scenario_id"],
            attempt=prepared.attempt_number,
        )
        evidence.write_json(
            "scenario.json",
            {
                "scope": "PX4_SITL_SIMULATION_ONLY",
                "requested_start_position_m": canonical["start_position_m"],
                "start_position_tolerance_m": canonical["start_position_tolerance_m"],
                "target_position_m": canonical["target_position_m"],
                "note": "requested start is scenario setup; measured telemetry is authoritative",
            },
        )
        import hashlib
        source_path = self.definition.source_path(self.project)
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        evidence.write_metadata(
            {
                "scope": "PX4_SITL_SIMULATION_ONLY",
                "source_sha256": source_hash,
                "web_console": "STEP37A",
                "verdict_authority": "independent_python_verifier",
                "ai_runtime_control": False,
                "execution_telemetry_scope": "capability_window_only",
            }
        )
        self._write_changed_files_marker(evidence)
        evidence.append_event("step37a_experiment_started")
        self._ensure_telemetry_artifact(attempt / "telemetry.csv")

        from src.adapters.px4_sitl_adapter import PX4SITLAdapter
        adapter = PX4SITLAdapter()
        recorder_task: asyncio.Task[int] | None = None
        stop_event = asyncio.Event()
        simulation_setup_completed = False

        try:
            await adapter.connect()

            requested_start = list(canonical["start_position_m"])
            # Gazebo sessions now spawn realistically on the ground.  NAV-001
            # itself remains an airborne navigation capability, so Single
            # Flight may perform an explicit simulation setup bootstrap before
            # the capability evidence window begins.  This is deliberately not
            # reported as a TAKEOFF-001 verdict; Mission Runtime remains the
            # path for independently verified Takeoff -> NAV composition.
            ground_bootstrap = abs(float(requested_start[2])) <= 1e-9
            setup_target_down = (
                float(canonical["target_position_m"][2])
                if ground_bootstrap
                else float(requested_start[2])
            )
            if setup_target_down >= 0:
                raise RuntimeError(
                    "Single Flight requires an airborne target altitude > 0 m"
                )

            self.store.update(
                run_id,
                phase=("AUTO_TAKEOFF_SETUP" if ground_bootstrap else "SIMULATION_SETUP"),
                auto_takeoff_bootstrap=ground_bootstrap,
                setup_target_down_m=setup_target_down,
            )
            evidence.append_event(
                "simulation_setup_started",
                {
                    "requested_start_position_m": requested_start,
                    "setup_target_down_m": setup_target_down,
                    "auto_takeoff_bootstrap": ground_bootstrap,
                    "scope": "PX4_SITL_SIMULATION_ONLY",
                },
            )

            await adapter.prepare_airborne(
                target_down_m=setup_target_down,
                timeout_s=min(
                    float(canonical["timeout_s"]),
                    30.0,
                ),
            )

            if ground_bootstrap:
                # Mission Runtime already has a verified TAKEOFF step followed
                # by a Hold post-action before NAV.  Interactive Single Flight
                # uses an unverified setup takeoff, so explicitly create the same
                # stable mode hand-off before NAV-001 opens its evidence window.
                self.store.update(run_id, phase="TAKEOFF_STABILIZE")
                evidence.append_event(
                    "takeoff_navigation_handoff_started",
                    {"mode": "HOLD"},
                )
                stabilize = getattr(adapter, "stabilize_after_takeoff", None)
                if callable(stabilize):
                    await stabilize(
                        timeout_s=min(float(canonical["timeout_s"]), 8.0)
                    )
                evidence.append_event(
                    "takeoff_navigation_handoff_completed",
                    {"mode": "HOLD"},
                )

            # Refresh telemetry after the mode hand-off.  The refreshed state is
            # the authoritative NAV start used by the deterministic start gate.
            state = await adapter.get_state()
            measured = await adapter.get_position()
            measured_start = [measured.x, measured.y, measured.z]

            evidence.append_event(
                "simulation_setup_completed",
                {
                    "measured_position_m": measured_start,
                    "airborne": state.airborne,
                    "localization_valid": state.localization_valid,
                    "auto_takeoff_bootstrap": ground_bootstrap,
                },
            )
            simulation_setup_completed = True
            evidence.write_json(
                "measured_initial_state.json",
                {
                    "position_m": measured_start,
                    "connected": state.connected,
                    "airborne": state.airborne,
                    "localization_valid": state.localization_valid,
                    "auto_takeoff_bootstrap": ground_bootstrap,
                    "source": "PX4_MAVSDK_TELEMETRY",
                },
            )
            gate_expected_start = (
                [requested_start[0], requested_start[1], setup_target_down]
                if ground_bootstrap
                else expected_start_position
            )
            start_error = math.dist(measured_start, gate_expected_start)
            self.store.update(
                run_id,
                phase="START_GATE",
                measured_start_position_m=measured_start,
                expected_start_position_m=gate_expected_start,
                requested_session_start_position_m=expected_start_position,
                start_error_m=start_error,
            )
            evidence.append_event(
                "measured_initial_state",
                {
                    "position_m": measured_start,
                    "expected_position_m": gate_expected_start,
                    "requested_session_start_position_m": expected_start_position,
                    "start_error_m": start_error,
                    "session_mode": session_mode,
                    "auto_takeoff_bootstrap": ground_bootstrap,
                },
            )

            if ground_bootstrap and start_error <= canonical["start_position_tolerance_m"]:
                # Ground spawn is a simulator setup condition, not the NAV-001
                # capability start.  Once takeoff setup finishes, the measured
                # airborne position becomes the authoritative NAV start.
                if not bounds.contains(*measured_start):
                    raise RuntimeError(
                        "measured airborne bootstrap start is outside permitted simulation region"
                    )
                canonical["start_position_m"] = list(measured_start)
                self.session.sync_measured_start(run_id, measured_start)
                self.store.update(
                    run_id,
                    effective_start_position_m=list(measured_start),
                    bootstrap_requested_ground_position_m=requested_start,
                )
                evidence.write_json(
                    "start_gate_result.json",
                    {
                        "overall": "PASS",
                        "reason": "ground_bootstrap_resynced_to_measured_airborne_start",
                        "requested_ground_position_m": requested_start,
                        "expected_airborne_position_m": gate_expected_start,
                        "measured_start_position_m": measured_start,
                        "error_m": start_error,
                        "tolerance_m": canonical["start_position_tolerance_m"],
                    },
                )
            elif session_mode == "CONTINUE":
                # For an interactive continuation the live measured position is
                # authoritative.  The previous verified final position is an
                # audit/continuity anchor, not a hard command start coordinate.
                if not bounds.contains(*measured_start):
                    raise RuntimeError(
                        "measured continuation start is outside permitted simulation region"
                    )
                canonical["start_position_m"] = list(measured_start)
                self.session.sync_measured_start(run_id, measured_start)
                self.store.update(
                    run_id,
                    effective_start_position_m=list(measured_start),
                )
                evidence.write_json(
                    "start_gate_result.json",
                    {
                        "overall": "PASS",
                        "reason": "continue_resynced_to_measured_start",
                        "expected_start_position_m": expected_start_position,
                        "measured_start_position_m": measured_start,
                        "drift_m": start_error,
                        "tolerance_m": canonical["start_position_tolerance_m"],
                    },
                )
            elif start_error > canonical["start_position_tolerance_m"]:
                gate_reason = (
                    "ground_bootstrap_outside_airborne_start_tolerance"
                    if ground_bootstrap
                    else "measured_start_outside_requested_start_tolerance"
                )
                failure = {
                    "task": attempt.name,
                    "position_error_m": None,
                    "elapsed_time_s": None,
                    "checks": {
                        "position": "NOT_RUN",
                        "timeout": "NOT_RUN",
                        "telemetry": "NOT_RUN",
                    },
                    "overall": "FAIL",
                    "reason": f"precondition_failed:{gate_reason}",
                }
                gate_payload = {
                    "overall": "FAIL",
                    "reason": gate_reason,
                    "expected_start_position_m": gate_expected_start,
                    "measured_start_position_m": measured_start,
                    "error_m": start_error,
                    "tolerance_m": canonical["start_position_tolerance_m"],
                }
                if ground_bootstrap:
                    gate_payload["requested_ground_position_m"] = requested_start
                else:
                    gate_payload["requested_start_position_m"] = expected_start_position
                evidence.write_json("start_gate_result.json", gate_payload)
                evidence.write_json(
                    "module_output.json",
                    {"status": "not_started", "reason": "START_GATE_FAILED"},
                )
                self._write_boundary_result(evidence, canonical, False, "start_gate")
                self._write_unit_test_marker(evidence)
                # Create a valid header-only telemetry artifact for evidence
                # completeness, but do not misclassify a preflight failure as
                # ``invalid_telemetry:telemetry_empty``.
                stop_event.set()
                await self._record_until_stop(
                    adapter, attempt / "telemetry.csv", stop_event, 0.5
                )
                evidence.write_json("verification.json", failure)
                evidence.append_event("verification_not_run", failure)
                try:
                    state_after_gate = await adapter.get_state()
                    if state_after_gate.airborne:
                        evidence.append_event("landing_started", {"reason": "start_gate_failed"})
                        await adapter.land_and_wait(timeout_s=30.0)
                        evidence.append_event("landing_completed", {"reason": "start_gate_failed"})
                finally:
                    try:
                        await adapter.abort()
                    except Exception:
                        pass
                self._validate_and_finalize(evidence)
                self.session.mark_reset_required()
                finished_at = datetime.now(timezone.utc).isoformat()
                created_at = (self.store.get(run_id) or {}).get("created_at")
                self.store.update(
                    run_id,
                    phase="PRECONDITION_FAILED",
                    status="FAIL",
                    verdict_authority="deterministic_start_gate",
                    result=failure,
                    finished_at=finished_at,
                )
                self._index_completed_run(
                    run_id, attempt=attempt, canonical=canonical,
                    verification=failure, final_position=None, status="FAIL",
                    created_at=created_at, finished_at=finished_at,
                )
                return
            else:
                evidence.write_json(
                    "start_gate_result.json",
                    {
                        "overall": "PASS",
                        "reason": "measured_start_within_requested_tolerance",
                        "requested_start_position_m": expected_start_position,
                        "measured_start_position_m": measured_start,
                        "error_m": start_error,
                        "tolerance_m": canonical["start_position_tolerance_m"],
                    },
                )

            if not (state.connected and state.airborne and state.localization_valid):
                raise RuntimeError(
                    f"PX4 preconditions failed: connected={state.connected}, airborne={state.airborne}, localization_valid={state.localization_valid}"
                )

            self._write_boundary_result(evidence, canonical, True, "input_and_start_gate")
            self._write_unit_test_marker(evidence)
            self.store.update(run_id, phase="RUNNING_CAPABILITY")
            recorder_task = asyncio.create_task(
                self._record_until_stop(
                    adapter,
                    attempt / "telemetry.csv",
                    stop_event,
                    1.0 / self.resource_policy.telemetry_rate_hz,
                    on_sample=lambda row: self._push_live_telemetry(run_id, row),
                    flush_every_samples=self.resource_policy.flush_every_samples,
                )
            )

            lifecycle_events: list[dict[str, Any]] = []
            result = await self.executor.execute(
                self.capability_id,
                adapter=adapter,
                input_data=prepared.canonical_input.to_dict(),
                log_hook=lambda state_value, context: lifecycle_events.append(
                    {"state": state_value.value, "context": context}
                ),
            )
            evidence.write_json("module_output.json", result.to_dict())
            evidence.write_json("lifecycle.json", {"events": lifecycle_events})
            evidence.append_event("module_completed", {"status": result.status_value, "error_code": result.error_code})

            # Freeze the exact capability telemetry window BEFORE verification.
            # Post-flight actions (continue/land) must never mutate the dataset
            # that produced the authoritative verdict.
            stop_event.set()
            if recorder_task:
                await recorder_task
                recorder_task = None
            evidence.append_event("execution_telemetry_frozen")

            self.store.update(run_id, phase="VERIFYING", module_result=result.to_dict())
            verification = self.executor.verify_attempt(
                self.capability_id, attempt, write_result=True
            )
            evidence.append_event("verification_completed", verification.to_dict())

            # Keep the vehicle stable between interactive runs.  Stopping
            # Offboard without entering Hold lets SITL drift, which can make the
            # next run fail its start gate before NAV-001 even executes.
            post_hold_position: list[float] | None = None
            try:
                await adapter.hold_position()
                evidence.append_event("post_run_hold_engaged")
                # Hold is outside the frozen NAV evidence window, but this fresh
                # sample is the best UI/session estimate of where the aircraft
                # actually sits while waiting for the next target.
                held = await adapter.get_position()
                post_hold_position = [held.x, held.y, held.z]
                self.store.update(
                    run_id,
                    post_hold_vehicle_position_m=list(post_hold_position),
                )
            except Exception as hold_exc:
                evidence.append_event(
                    "post_run_hold_failed", {"error": str(hold_exc)}
                )
                await adapter.abort()

            (attempt / "report.md").write_text(
                "# NAV-001 Interactive Experiment\n\n"
                f"- Scope: PX4 SITL simulation only\n"
                f"- Scenario: {canonical['scenario_id']}\n"
                f"- Requested start: {canonical['start_position_m']}\n"
                f"- Target: {canonical['target_position_m']}\n"
                f"- Verifier overall: {verification.overall}\n"
                f"- Position error (m): {verification.position_error_m}\n"
                f"- Elapsed time (s): {verification.elapsed_time_s}\n"
                "- Post-run action: vehicle kept in simulation for Continue/Land\n"
                "- Verdict authority: independent_python_verifier\n",
                encoding="utf-8",
            )
            self._validate_and_finalize(evidence)
            final_position = self._navigation_final_position(result.to_dict())
            self.session.mark_run_completed(
                run_id,
                final_position,
                verification.overall,
                current_position_m=post_hold_position,
            )
            cleanup_error = await self._close_adapter(adapter)
            finished_at = datetime.now(timezone.utc).isoformat()
            created_at = (self.store.get(run_id) or {}).get("created_at")
            self.store.update(
                run_id,
                phase="VERIFIED",
                verified_final_position_m=final_position,
                status=verification.overall,
                result=verification.to_dict(),
                report_path=str(attempt / "report.md"),
                adapter_cleanup_error=cleanup_error,
                finished_at=finished_at,
            )
            self._index_completed_run(
                run_id,
                attempt=attempt,
                canonical=canonical,
                verification=verification.to_dict(),
                final_position=final_position,
                status=verification.overall,
                created_at=created_at,
                finished_at=finished_at,
            )
        except Exception as exc:
            original_error = f"{type(exc).__name__}: {exc}"
            setup_failure = not simulation_setup_completed and recorder_task is None
            if not evidence.is_finalized:
                try:
                    evidence.write_json(
                        "runtime_error.json",
                        {"type": type(exc).__name__, "message": str(exc)},
                    )
                    if setup_failure:
                        evidence.append_event(
                            "simulation_setup_failed",
                            {"error": original_error},
                        )
                except Exception:
                    pass
            # Freeze capability evidence first. Cleanup/landing is outside the
            # execution window and must never change the verifier dataset.
            stop_event.set()
            if recorder_task:
                try:
                    await recorder_task
                except Exception:
                    pass
                recorder_task = None
            if not evidence.is_finalized:
                try:
                    evidence.append_event("execution_telemetry_frozen", {"reason": "runtime_error"})
                except Exception:
                    pass

            # Runtime errors leave vehicle state uncertain, so attempt a safe
            # simulation-only landing after the telemetry window is closed.
            try:
                state_after_error = await adapter.get_state()
                if state_after_error.airborne:
                    evidence.append_event("landing_started", {"reason": "runtime_error"})
                    await adapter.land_and_wait(timeout_s=30.0)
                    evidence.append_event("landing_completed", {"reason": "runtime_error"})
            except Exception as landing_exc:
                if not evidence.is_finalized:
                    evidence.append_event(
                        "landing_failed",
                        {"reason": "runtime_error", "error": str(landing_exc)},
                    )
            try:
                await adapter.abort()
            except Exception:
                pass

            if not evidence.is_finalized:
                if not (attempt / "module_output.json").exists():
                    evidence.write_json(
                        "module_output.json",
                        (
                            {
                                "status": "not_started",
                                "reason": "SIMULATION_SETUP_FAILED",
                                "error": str(exc),
                            }
                            if setup_failure
                            else {"status": "failed", "error": str(exc)}
                        ),
                    )
                if not (attempt / "boundary_result.json").exists():
                    self._write_boundary_result(
                        evidence,
                        canonical,
                        False,
                        "simulation_setup" if setup_failure else "runtime_error",
                    )
                if not (attempt / "unit_test_result.json").exists():
                    self._write_unit_test_marker(evidence)

                if setup_failure:
                    # No capability execution window existed, so invoking the
                    # NAV verifier on a header-only telemetry file would
                    # misclassify a setup/precondition failure as
                    # invalid_telemetry:telemetry_empty.  Record a deterministic
                    # NOT_RUN verdict instead while preserving runtime_error.json
                    # as the root cause.
                    result_payload = {
                        "task": attempt.name,
                        "position_error_m": None,
                        "elapsed_time_s": None,
                        "checks": {
                            "position": "NOT_RUN",
                            "timeout": "NOT_RUN",
                            "telemetry": "NOT_RUN",
                        },
                        "overall": "FAIL",
                        "reason": "precondition_failed:simulation_setup_error",
                    }
                    evidence.write_json("verification.json", result_payload)
                    evidence.append_event("verification_not_run", result_payload)
                else:
                    try:
                        verification = self.executor.verify_attempt(
                            self.capability_id, attempt, write_result=True
                        )
                        evidence.append_event("verification_completed", verification.to_dict())
                    except Exception as verifier_exc:
                        verification = None
                        fallback_verification = {
                            "task": attempt.name,
                            "position_error_m": None,
                            "elapsed_time_s": None,
                            "checks": {"position": "FAIL", "timeout": "FAIL", "telemetry": "FAIL"},
                            "overall": "FAIL",
                            "reason": "verifier_exception:" + str(verifier_exc),
                        }
                        evidence.write_json("verification.json", fallback_verification)
                        evidence.append_event("verification_failed", {"error": str(verifier_exc)})

                    result_payload = (
                        verification.to_dict()
                        if verification is not None
                        else json.loads((attempt / "verification.json").read_text(encoding="utf-8"))
                    )

                try:
                    self._validate_and_finalize(evidence)
                except Exception as finalize_exc:
                    self.session.mark_reset_required()
                    cleanup_error = await self._close_adapter(adapter)
                    self.store.update(
                        run_id,
                        phase="ERROR",
                        status="FAILED",
                        error=original_error,
                        evidence_error=f"{type(finalize_exc).__name__}: {finalize_exc}",
                        adapter_cleanup_error=cleanup_error,
                        result=result_payload,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    return

                capability_result = locals().get("result")
                final_position = None
                if capability_result is not None and hasattr(capability_result, "to_dict"):
                    final_position = self._navigation_final_position(capability_result.to_dict())
                if final_position is None:
                    self.session.mark_reset_required()
                else:
                    self.session.mark_run_completed(run_id, final_position, "FAIL")

                cleanup_error = await self._close_adapter(adapter)
                finished_at = datetime.now(timezone.utc).isoformat()
                created_at = (self.store.get(run_id) or {}).get("created_at")
                self.store.update(
                    run_id,
                    phase="VERIFIED",
                    status="FAIL",
                    error=original_error,
                    result=result_payload,
                    verified_final_position_m=final_position,
                    adapter_cleanup_error=cleanup_error,
                    finished_at=finished_at,
                )
                self._index_completed_run(
                    run_id, attempt=attempt, canonical=canonical,
                    verification=result_payload, final_position=final_position, status="FAIL",
                    created_at=created_at, finished_at=finished_at,
                )
        finally:
            # Last-resort cleanup. Normal success/failure paths close the adapter
            # before exposing a terminal run status, so this is usually a no-op.
            cleanup_error = await self._close_adapter(adapter)
            if cleanup_error:
                self.store.update(run_id, adapter_cleanup_error=cleanup_error)

    @staticmethod
    def _navigation_final_position(payload: dict[str, Any] | None) -> list[float] | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        final = data.get("final_position")
        if not isinstance(final, list) or len(final) != 3:
            return None
        try:
            return [float(v) for v in final]
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def _record_until_stop(
        adapter: PX4SITLAdapter,
        path: Path,
        stop: asyncio.Event,
        interval_s: float,
        *,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        flush_every_samples: int = 5,
    ) -> int:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        if flush_every_samples <= 0:
            raise ValueError("flush_every_samples must be > 0")
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["timestamp", "x", "y", "z", "vx", "vy", "vz", "vehicle_state", "localization_valid"]
        count = 0
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            f.flush()
            while not stop.is_set():
                sample: TelemetrySample = await adapter.get_telemetry_sample()
                row = {
                    "timestamp": sample.timestamp_s,
                    "x": sample.x,
                    "y": sample.y,
                    "z": sample.z,
                    "vx": sample.vx,
                    "vy": sample.vy,
                    "vz": sample.vz,
                    "vehicle_state": sample.vehicle_state,
                    "localization_valid": sample.localization_valid,
                }
                writer.writerow(row)
                count += 1
                if on_sample is not None:
                    on_sample(row)
                if count % flush_every_samples == 0:
                    f.flush()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval_s)
                except asyncio.TimeoutError:
                    pass
            f.flush()
        return count

    @staticmethod
    def _write_boundary_result(evidence: AttemptEvidence, canonical: dict[str, Any], passed: bool, reason: str) -> None:
        evidence.write_json(
            "boundary_result.json",
            {
                "overall": "PASS" if passed else "FAIL",
                "scope": "SIMULATION_ONLY",
                "reason": reason,
                "target_position_m": canonical["target_position_m"],
                "start_position_m": canonical["start_position_m"],
            },
        )

    @staticmethod
    def _write_unit_test_marker(evidence: AttemptEvidence) -> None:
        evidence.write_json(
            "unit_test_result.json",
            {
                "status": "NOT_RUN_IN_RUNTIME_EXPERIMENT",
                "note": "Project unit suite is a pre-experiment gate; runtime console does not mutate it.",
            },
        )


def latest_telemetry(attempt_dir: Path, limit: int = 120) -> list[dict[str, Any]]:
    """Read only the tail of telemetry.csv instead of materializing the file.

    Active runs normally use the manager's ring buffer. This bounded fallback is
    used for finalized/history runs and stays cheap even when telemetry is large.
    """
    path = Path(attempt_dir) / "telemetry.csv"
    limit = max(1, min(int(limit), 5000))
    if not path.is_file():
        return []

    with path.open("rb") as f:
        header_bytes = f.readline()
        data_start = f.tell()
        if not header_bytes:
            return []
        f.seek(0, 2)
        end = f.tell()
        if end <= data_start:
            return []

        pos = end
        chunks: list[bytes] = []
        newline_count = 0
        block_size = 8192
        while pos > data_start and newline_count <= limit:
            size = min(block_size, pos - data_start)
            pos -= size
            f.seek(pos)
            chunk = f.read(size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    data = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    lines = data.splitlines()
    if pos > data_start and lines:
        # The first line starts in the middle of a CSV record.
        lines = lines[1:]
    lines = lines[-limit:]
    if not lines:
        return []
    header = header_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
    reader = csv.DictReader([header, *lines])
    return list(reader)
