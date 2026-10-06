from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.runtime_paths import runs_root
from orchestration.capability_experiment import CapabilityExperimentRunner
from orchestration.runtime_input_bridge import next_attempt_number
from src.evidence.attempt import AttemptEvidence
from src.mission.model import MissionPlan
from src.verification.mission_verifier import MissionVerifier


@dataclass(frozen=True)
class MissionExperimentResult:
    run_id: str
    mission_id: str
    attempt_dir: Path
    step_results: tuple[dict[str, Any], ...]
    verification: dict[str, Any]
    adapter_cleanup_error: str | None = None

    @property
    def overall(self) -> str:
        return str(self.verification.get("overall") or "FAIL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "attempt_dir": str(self.attempt_dir),
            "step_results": list(self.step_results),
            "verification": dict(self.verification),
            "adapter_cleanup_error": self.adapter_cleanup_error,
            "overall": self.overall,
        }


class MissionExperimentRunner:
    """Execute MISSION-001 over independently verified capability windows.

    Mission execution reuses one adapter across the whole vehicle session. Each
    child still gets its own frozen telemetry/evidence attempt and independent
    verifier. MissionVerifier, not this class, owns the mission PASS/FAIL verdict.
    """

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self.runs_root = runs_root(self.project)
        self.capabilities = CapabilityExperimentRunner(self.project)
        self.verifier = MissionVerifier(self.project)

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    async def _close_adapter(adapter: Any) -> str | None:
        close = getattr(adapter, "close", None)
        if close is None:
            return None
        try:
            value = close()
            if asyncio.iscoroutine(value):
                await value
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    def _child_relpath(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.runs_root.resolve()))

    async def run(
        self,
        *,
        mission_data: dict[str, Any],
        scenario_id: str,
        adapter: Any,
        step_hook: Callable[[dict[str, Any]], None] | None = None,
        step_event_hook: Callable[[dict[str, Any]], None] | None = None,
    ) -> MissionExperimentResult:
        plan = MissionPlan.from_dict(mission_data)
        for step in plan.steps:
            self.capabilities.registry.get_definition(step.capability_id)

        attempt_number = next_attempt_number(
            runs_root=self.runs_root,
            task_id=plan.mission_id,
            scenario_id=scenario_id,
        )
        evidence = AttemptEvidence(
            root=self.runs_root,
            task_id=plan.mission_id,
            scenario_id=scenario_id,
            attempt=attempt_number,
        )
        attempt = evidence.create()
        run_id = datetime.now(timezone.utc).strftime(
            "MISSION001-%Y%m%dT%H%M%S-%fZ"
        )
        evidence.write_metadata(
            {
                "run_id": run_id,
                "mission_id": plan.mission_id,
                "scope": "PX4_SITL_SIMULATION_ONLY",
                "verdict_authority": "independent_python_mission_verifier",
                "child_verdict_authority": "independent_python_verifier",
                "child_adapter_lifecycle": "shared_one_adapter",
                "step_count": len(plan.steps),
            }
        )
        evidence.write_json("mission_input.json", plan.to_dict())
        evidence.append_event(
            "mission_started",
            {
                "run_id": run_id,
                "step_count": len(plan.steps),
                "frame": plan.frame,
            },
        )

        self.capabilities.runs_root = self.runs_root
        self.verifier.runs_root = self.runs_root

        step_results: list[dict[str, Any]] = []
        adapter_cleanup_error: str | None = None
        try:
            for sequence, step in enumerate(plan.steps, start=1):
                child_scenario = f"{scenario_id}__{sequence:02d}_{step.step_id}"
                started = {
                    "event": "started",
                    "state": "RUNNING",
                    "sequence": sequence,
                    "step_id": step.step_id,
                    "capability_id": step.capability_id,
                    "scenario_id": child_scenario,
                }
                evidence.append_event(
                    "mission_step_started",
                    {
                        "sequence": sequence,
                        "step_id": step.step_id,
                        "capability_id": step.capability_id,
                    },
                )
                if step_event_hook is not None:
                    step_event_hook(dict(started))
                try:
                    result = await self.capabilities.run(
                        step.capability_id,
                        input_data=dict(step.input_data),
                        scenario_id=child_scenario,
                        adapter=adapter,
                        post_action=step.post_action,
                        close_adapter=False,
                    )
                except Exception as exc:
                    step_results.append(
                        {
                            "sequence": sequence,
                            "step_id": step.step_id,
                            "capability_id": step.capability_id,
                            "scenario_id": child_scenario,
                            "overall": "FAIL",
                            "attempt_relpath": None,
                            "finalized_sha256": None,
                            "verification_checks": {},
                            "post_action_error": None,
                            "runner_error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    evidence.append_event(
                        "mission_step_exception",
                        {
                            "sequence": sequence,
                            "step_id": step.step_id,
                            "capability_id": step.capability_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    if step_hook is not None:
                        step_hook(dict(step_results[-1]))
                    if step_event_hook is not None:
                        failed_event = dict(step_results[-1])
                        failed_event.update({"event": "completed", "state": "FAIL"})
                        step_event_hook(failed_event)
                    break

                finalized = result.attempt_dir / "finalized.json"
                record = {
                    "sequence": sequence,
                    "step_id": step.step_id,
                    "capability_id": step.capability_id,
                    "scenario_id": child_scenario,
                    "overall": result.overall,
                    "attempt_relpath": self._child_relpath(result.attempt_dir),
                    "finalized_sha256": (
                        self._sha256(finalized) if finalized.is_file() else None
                    ),
                    "verification_checks": dict(
                        result.verification.get("checks") or {}
                    ),
                    "post_action_error": result.post_action_error,
                    "runner_error": None,
                }
                step_results.append(record)
                if step_hook is not None:
                    step_hook(dict(record))
                if step_event_hook is not None:
                    completed_event = dict(record)
                    completed_event.update({
                        "event": "completed",
                        "state": result.overall,
                    })
                    step_event_hook(completed_event)
                evidence.append_event(
                    "mission_step_completed",
                    {
                        "sequence": sequence,
                        "step_id": step.step_id,
                        "capability_id": step.capability_id,
                        "overall": result.overall,
                        "attempt_relpath": record["attempt_relpath"],
                        "post_action_error": result.post_action_error,
                    },
                )
                if result.overall != "PASS" or result.post_action_error:
                    break
        finally:
            adapter_cleanup_error = await self._close_adapter(adapter)

        evidence.write_json("mission_steps.json", {"steps": step_results})
        if adapter_cleanup_error:
            evidence.write_json(
                "adapter_cleanup_error.json",
                {"error": adapter_cleanup_error},
            )
        evidence.append_event(
            "mission_execution_finished",
            {
                "executed_steps": len(step_results),
                "adapter_cleanup_error": adapter_cleanup_error,
            },
        )

        verification = self.verifier.verify_attempt(attempt, write_result=True)
        verification_dict = verification.to_dict()
        evidence.append_event(
            "mission_verification_completed",
            verification_dict,
        )
        evidence.finalize()

        return MissionExperimentResult(
            run_id=run_id,
            mission_id=plan.mission_id,
            attempt_dir=attempt,
            step_results=tuple(step_results),
            verification=verification_dict,
            adapter_cleanup_error=adapter_cleanup_error,
        )
