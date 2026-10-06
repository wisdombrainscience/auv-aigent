from __future__ import annotations

import asyncio
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import OrderedDict, deque
from typing import Any

from src.runtime_paths import runs_root
from orchestration.mission_experiment import MissionExperimentRunner
from orchestration.step37a_simulation import SimulationSupervisor
from orchestration.web_experiment import ExperimentStore
from src.adapters.px4_sitl_adapter import PX4SITLAdapter
from src.resources.manager import ResourceManager, load_resource_policy
from user_interface.policy import load_input_policy


class MissionWebManager:
    """Localhost-only Mission Builder runtime facade.

    The browser only builds/starts a mission and displays status. Child
    capability verifiers and MissionVerifier remain the only PASS/FAIL
    authorities.
    """

    def __init__(
        self, project: Path, simulation: SimulationSupervisor | None = None
    ) -> None:
        self.project = Path(project).resolve()
        self.runs_root = runs_root(self.project)
        self.policy = load_input_policy(self.project / "config" / "input_policy.yaml")
        self.resource_policy = load_resource_policy(
            self.project / "config" / "resource_policy.yaml"
        )
        self.resources = ResourceManager(self.runs_root, self.resource_policy)
        self.store = ExperimentStore(max_items=self.resource_policy.max_in_memory_runs)
        self.simulation = simulation if simulation is not None else SimulationSupervisor(self.project)
        self._active_lock = threading.Lock()
        self._active_run: str | None = None
        self._trajectory_lock = threading.Lock()
        self._live_trajectory: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        out = float(value)
        if not math.isfinite(out):
            raise ValueError(f"{name} must be finite")
        return out

    def has_active_run(self) -> bool:
        with self._active_lock:
            if self._active_run is None:
                return False
            item = self.store.get(self._active_run)
            return bool(item and item.get("status") in {"QUEUED", "STARTING_SIM", "RUNNING"})

    def get(
        self, run_id: str, *, include_trajectory: bool = True
    ) -> dict[str, Any] | None:
        item = self.store.get(run_id)
        if item is None:
            return None
        if include_trajectory:
            with self._trajectory_lock:
                buffer = self._live_trajectory.get(run_id)
                if buffer is not None:
                    self._live_trajectory.move_to_end(run_id)
                    item["trajectory"] = list(buffer)
        else:
            item.pop("trajectory", None)
        return item

    def latest_trajectory_for_run(
        self,
        run_id: str,
        *,
        since_timestamp: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        cap = (
            self.resource_policy.telemetry_batch_limit
            if limit is None
            else max(1, min(int(limit), self.resource_policy.live_buffer_points))
        )
        with self._trajectory_lock:
            buffer = self._live_trajectory.get(run_id)
            if buffer is None:
                return []
            self._live_trajectory.move_to_end(run_id)
            rows = list(buffer)
        if since_timestamp is not None:
            rows = [
                row
                for row in rows
                if float(row.get("timestamp", 0.0)) > since_timestamp
            ]
        return rows[-cap:]

    def _create_trajectory_buffer(self, run_id: str) -> None:
        with self._trajectory_lock:
            self._live_trajectory[run_id] = deque(
                maxlen=self.resource_policy.live_buffer_points
            )
            self._live_trajectory.move_to_end(run_id)
            while len(self._live_trajectory) > self.resource_policy.max_in_memory_runs:
                self._live_trajectory.popitem(last=False)

    def validate_request(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("mission request must be a JSON object")

        scenario_id = str(raw.get("scenario_id") or "MISSION-UI").strip()
        if not scenario_id or len(scenario_id) > 80:
            raise ValueError("scenario_id must be 1..80 characters")

        home_raw = raw.get("home_position_m", [0.0, 0.0])
        if not isinstance(home_raw, list) or len(home_raw) != 2:
            raise ValueError("home_position_m must be [north,east]")
        home = [self._finite(home_raw[0], "home north"), self._finite(home_raw[1], "home east")]

        waypoints_raw = raw.get("waypoints")
        if not isinstance(waypoints_raw, list) or not waypoints_raw:
            raise ValueError("mission requires at least one waypoint")
        if len(waypoints_raw) > 20:
            raise ValueError("mission supports at most 20 waypoints")

        waypoints: list[dict[str, float]] = []
        for index, item in enumerate(waypoints_raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"waypoint {index} must be an object")
            north = self._finite(item.get("north"), f"waypoint {index} north")
            east = self._finite(item.get("east"), f"waypoint {index} east")
            altitude = self._finite(item.get("altitude"), f"waypoint {index} altitude")
            if altitude <= 0:
                raise ValueError(f"waypoint {index} altitude must be > 0")
            waypoints.append({"north": north, "east": east, "altitude": altitude})

        takeoff_altitude = self._finite(raw.get("takeoff_altitude_m", 3.0), "takeoff_altitude_m")
        return_altitude = self._finite(raw.get("return_altitude_m", 3.0), "return_altitude_m")
        position_tolerance = self._finite(raw.get("position_tolerance_m", 0.5), "position_tolerance_m")
        altitude_tolerance = self._finite(raw.get("altitude_tolerance_m", 0.5), "altitude_tolerance_m")
        horizontal_tolerance = self._finite(raw.get("horizontal_tolerance_m", 1.0), "horizontal_tolerance_m")
        ground_altitude_tolerance = self._finite(
            raw.get("ground_altitude_tolerance_m", 0.5), "ground_altitude_tolerance_m"
        )
        continuity_tolerance = self._finite(raw.get("continuity_tolerance_m", 1.0), "continuity_tolerance_m")
        terminal_home_tolerance = self._finite(
            raw.get("terminal_home_tolerance_m", 1.0), "terminal_home_tolerance_m"
        )
        timeout_s = self._finite(raw.get("timeout_s", 30.0), "timeout_s")

        positive = {
            "takeoff_altitude_m": takeoff_altitude,
            "return_altitude_m": return_altitude,
            "position_tolerance_m": position_tolerance,
            "altitude_tolerance_m": altitude_tolerance,
            "horizontal_tolerance_m": horizontal_tolerance,
            "ground_altitude_tolerance_m": ground_altitude_tolerance,
            "continuity_tolerance_m": continuity_tolerance,
            "terminal_home_tolerance_m": terminal_home_tolerance,
            "timeout_s": timeout_s,
        }
        if any(value <= 0 for value in positive.values()):
            raise ValueError("altitudes, tolerances and timeout must be > 0")
        self.resources.assert_can_start(timeout_s)

        bounds = self.policy.test_area
        if not bounds.contains(home[0], home[1], 0.0):
            raise ValueError("home is outside the permitted simulation region")
        if not bounds.contains(home[0], home[1], -takeoff_altitude):
            raise ValueError("takeoff altitude is outside the permitted simulation region")
        if not bounds.contains(home[0], home[1], -return_altitude):
            raise ValueError("return altitude is outside the permitted simulation region")
        for index, waypoint in enumerate(waypoints, start=1):
            if not bounds.contains(
                waypoint["north"], waypoint["east"], -waypoint["altitude"]
            ):
                raise ValueError(
                    f"waypoint {index} is outside the permitted simulation region"
                )

        return {
            "scenario_id": scenario_id,
            "home_position_m": home,
            "waypoints": waypoints,
            **positive,
        }

    @staticmethod
    def build_mission_plan(request: dict[str, Any]) -> dict[str, Any]:
        home_n, home_e = request["home_position_m"]
        takeoff_alt = request["takeoff_altitude_m"]
        return_alt = request["return_altitude_m"]
        timeout_s = request["timeout_s"]
        altitude_tolerance = request["altitude_tolerance_m"]
        horizontal_tolerance = request["horizontal_tolerance_m"]

        steps: list[dict[str, Any]] = [
            {
                "step_id": "takeoff",
                "capability_id": "TAKEOFF-001",
                "post_action": "hold",
                "input": {
                    "task_id": "TAKEOFF-001",
                    "frame": "LOCAL_NED",
                    "target_altitude_m": takeoff_alt,
                    "altitude_tolerance_m": altitude_tolerance,
                    "horizontal_tolerance_m": horizontal_tolerance,
                    "timeout_s": timeout_s,
                },
            }
        ]

        for index, waypoint in enumerate(request["waypoints"], start=1):
            steps.append(
                {
                    "step_id": f"waypoint_{index:02d}",
                    "capability_id": "NAV-001",
                    "post_action": "hold",
                    "input": {
                        "task_id": "NAV-001",
                        "frame": "LOCAL_NED",
                        "target_position_m": [
                            waypoint["north"],
                            waypoint["east"],
                            -waypoint["altitude"],
                        ],
                        "position_tolerance_m": request["position_tolerance_m"],
                        "timeout_s": timeout_s,
                    },
                }
            )

        steps.extend(
            [
                {
                    "step_id": "return_home",
                    "capability_id": "RETURN-HOME-001",
                    "post_action": "hold",
                    "input": {
                        "task_id": "RETURN-HOME-001",
                        "frame": "LOCAL_NED",
                        "home_position_m": [home_n, home_e],
                        "return_altitude_m": return_alt,
                        "horizontal_tolerance_m": request["position_tolerance_m"],
                        "altitude_tolerance_m": altitude_tolerance,
                        "timeout_s": timeout_s,
                    },
                },
                {
                    "step_id": "land",
                    "capability_id": "LAND-001",
                    "post_action": "none",
                    "input": {
                        "task_id": "LAND-001",
                        "frame": "LOCAL_NED",
                        "ground_altitude_tolerance_m": request[
                            "ground_altitude_tolerance_m"
                        ],
                        "horizontal_tolerance_m": horizontal_tolerance,
                        "timeout_s": timeout_s,
                    },
                },
            ]
        )

        return {
            "mission_id": "MISSION-001",
            "frame": "LOCAL_NED",
            "continuity_tolerance_m": request["continuity_tolerance_m"],
            "terminal_home_position_m": [home_n, home_e],
            "terminal_home_tolerance_m": request["terminal_home_tolerance_m"],
            "steps": steps,
        }

    def start(self, raw: dict[str, Any]) -> str:
        request = self.validate_request(raw)
        run_id = datetime.now(timezone.utc).strftime("MISSIONUI-%Y%m%dT%H%M%S-%fZ")
        mission_plan = self.build_mission_plan(request)

        with self._active_lock:
            if self._active_run is not None:
                current = self.store.get(self._active_run)
                if current and current.get("status") in {"QUEUED", "STARTING_SIM", "RUNNING"}:
                    raise RuntimeError("a mission is already running")
                self._active_run = None
            self._active_run = run_id
            self.store.create(
                run_id,
                {
                    "run_id": run_id,
                    "status": "QUEUED",
                    "phase": "QUEUED",
                    "request": request,
                    "mission_plan": mission_plan,
                    "steps": [],
                    "current_step": None,
                    "live_position_m": None,
                    "live_vehicle_state": None,
                    "trajectory": [],
                    "progress_percent": 0.0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self._create_trajectory_buffer(run_id)

        thread = threading.Thread(
            target=self._thread_entry,
            args=(run_id, request, mission_plan),
            name=f"{run_id}-worker",
            daemon=True,
        )
        thread.start()
        return run_id

    def _thread_entry(
        self,
        run_id: str,
        request: dict[str, Any],
        mission_plan: dict[str, Any],
    ) -> None:
        try:
            asyncio.run(self._run(run_id, request, mission_plan))
        except Exception as exc:
            self.simulation.stop()
            self.store.update(
                run_id,
                status="FAILED",
                phase="ERROR",
                simulation=self.simulation.live_status(),
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            try:
                self.simulation.stop()
            finally:
                with self._active_lock:
                    if self._active_run == run_id:
                        self._active_run = None

    async def _monitor_live_telemetry(
        self,
        run_id: str,
        adapter: PX4SITLAdapter,
        stop_event: asyncio.Event,
        current_sequence: dict[str, int | None],
    ) -> None:
        """Publish a bounded live trajectory while the mission executes.

        The monitor only reads cached adapter telemetry; it never controls the
        vehicle and never contributes to PASS/FAIL. Raw verifier evidence stays
        in each child capability attempt.
        """
        while not stop_event.is_set():
            try:
                sample = await adapter.get_telemetry_sample()
                point = {
                    "timestamp": float(sample.timestamp_s),
                    "x": float(sample.x),
                    "y": float(sample.y),
                    "z": float(sample.z),
                    "vehicle_state": sample.vehicle_state,
                    "step_sequence": current_sequence.get("value"),
                }
                with self._trajectory_lock:
                    trajectory = self._live_trajectory.get(run_id)
                    if trajectory is None:
                        trajectory = deque(
                            maxlen=self.resource_policy.live_buffer_points
                        )
                        self._live_trajectory[run_id] = trajectory
                    trajectory.append(point)
                    self._live_trajectory.move_to_end(run_id)
                # Keep only scalar live state in the job registry. The bounded
                # trajectory lives in its own ring buffer and is transferred
                # incrementally by the web API. This avoids copying hundreds of
                # dicts into the store five times per second.
                self.store.update(
                    run_id,
                    live_position_m=[point["x"], point["y"], point["z"]],
                    live_vehicle_state=point["vehicle_state"],
                    last_telemetry_at=datetime.now(timezone.utc).isoformat(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Adapter is legitimately disconnected while the first child is
                # starting and after the last child closes it.
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.20)
            except asyncio.TimeoutError:
                pass

    async def _run(
        self,
        run_id: str,
        request: dict[str, Any],
        mission_plan: dict[str, Any],
    ) -> None:
        home_n, home_e = request["home_position_m"]
        self.store.update(run_id, status="STARTING_SIM", phase="STARTING_SIM")
        simulation = self.simulation.start_new([home_n, home_e, -0.0])
        self.store.update(
            run_id,
            status="RUNNING",
            phase="RUNNING_MISSION",
            simulation=simulation,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        adapter = PX4SITLAdapter()
        monitor_stop = asyncio.Event()
        current_sequence: dict[str, int | None] = {"value": None}
        monitor_task = asyncio.create_task(
            self._monitor_live_telemetry(
                run_id, adapter, monitor_stop, current_sequence
            ),
            name=f"{run_id}-live-telemetry",
        )
        total_steps = len(mission_plan.get("steps") or [])

        def step_event_hook(event: dict[str, Any]) -> None:
            sequence = int(event.get("sequence") or 0)
            current_sequence["value"] = sequence or None
            current = self.store.get(run_id) or {}
            steps = list(current.get("steps") or [])
            while len(steps) < total_steps:
                steps.append({})
            if sequence > 0:
                previous = dict(steps[sequence - 1] or {})
                previous.update(dict(event))
                steps[sequence - 1] = previous
            event_name = str(event.get("event") or "")
            if event_name == "started":
                phase = f"STEP_{sequence}_RUNNING"
                completed = sum(
                    1
                    for item in steps
                    if str((item or {}).get("state") or (item or {}).get("overall"))
                    in {"PASS", "FAIL"}
                )
                progress = 100.0 * completed / max(1, total_steps)
            else:
                phase = f"STEP_{sequence}_COMPLETED"
                completed = sum(
                    1
                    for item in steps
                    if str((item or {}).get("state") or (item or {}).get("overall"))
                    in {"PASS", "FAIL"}
                )
                progress = 100.0 * completed / max(1, total_steps)
            self.store.update(
                run_id,
                steps=steps,
                current_step=dict(event),
                phase=phase,
                progress_percent=progress,
            )

        runner = MissionExperimentRunner(self.project)
        try:
            result = await runner.run(
                mission_data=mission_plan,
                scenario_id=request["scenario_id"],
                adapter=adapter,
                step_event_hook=step_event_hook,
            )
        finally:
            monitor_stop.set()
            try:
                await asyncio.wait_for(monitor_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)

        # Mission Builder owns this simulation lifecycle. Stop only after all
        # child evidence and the independent mission verdict are finalized.
        self.simulation.stop()
        self.store.update(
            run_id,
            status=result.overall,
            phase="VERIFIED",
            simulation=self.simulation.live_status(),
            steps=[dict(x, state=x.get("overall")) for x in result.step_results],
            current_step=None,
            progress_percent=100.0,
            result=result.verification,
            attempt_dir=str(result.attempt_dir),
            mission_run_id=result.run_id,
            adapter_cleanup_error=result.adapter_cleanup_error,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
