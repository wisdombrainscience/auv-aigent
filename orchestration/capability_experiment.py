from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.runtime_paths import runs_root
from orchestration.runtime_input_bridge import next_attempt_number
from src.capabilities.catalog import build_default_registry
from src.core.executor import CapabilityExecutor
from src.evidence.attempt import AttemptEvidence
from src.evidence.contract import EvidenceContractValidator
from src.resources.manager import ResourceManager, load_resource_policy


TELEMETRY_FIELDS = [
    "timestamp",
    "x",
    "y",
    "z",
    "vx",
    "vy",
    "vz",
    "vehicle_state",
    "localization_valid",
]


@dataclass(frozen=True)
class CapabilityExperimentResult:
    run_id: str
    capability_id: str
    attempt_dir: Path
    module_result: dict[str, Any]
    verification: dict[str, Any]
    evidence_complete: bool
    adapter_cleanup_error: str | None = None
    post_action_error: str | None = None

    @property
    def overall(self) -> str:
        return str(self.verification.get("overall") or "FAIL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "capability_id": self.capability_id,
            "attempt_dir": str(self.attempt_dir),
            "module_result": self.module_result,
            "verification": self.verification,
            "evidence_complete": self.evidence_complete,
            "adapter_cleanup_error": self.adapter_cleanup_error,
            "post_action_error": self.post_action_error,
            "overall": self.overall,
        }


class CapabilityExperimentRunner:
    """Generic single-capability evidence runner.

    It deliberately does not decide PASS/FAIL.  The registered deterministic
    verifier owns the verdict.  Simulator lifecycle is also external; callers
    may reset/start a simulator before invoking this runner.
    """

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self.runs_root = runs_root(self.project)
        self.registry = build_default_registry(self.project)
        self.executor = CapabilityExecutor(self.registry, self.project)
        self.resource_policy = load_resource_policy(
            self.project / "config" / "resource_policy.yaml"
        )
        self.resources = ResourceManager(self.runs_root, self.resource_policy)

    @staticmethod
    def _row(sample: Any) -> dict[str, Any]:
        return {
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

    @staticmethod
    def _write_header_and_sample(path: Path, sample: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TELEMETRY_FIELDS)
            writer.writeheader()
            writer.writerow(CapabilityExperimentRunner._row(sample))
            f.flush()

    @staticmethod
    def _write_header_only(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=TELEMETRY_FIELDS).writeheader()

    @staticmethod
    def _append_sample(path: Path, sample: Any) -> None:
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TELEMETRY_FIELDS)
            writer.writerow(CapabilityExperimentRunner._row(sample))
            f.flush()

    async def _record_until_stop(
        self,
        adapter: Any,
        path: Path,
        stop_event: asyncio.Event,
    ) -> int:
        interval = 1.0 / self.resource_policy.telemetry_rate_hz
        count = 0
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TELEMETRY_FIELDS)
            while not stop_event.is_set():
                sample = await adapter.get_telemetry_sample()
                writer.writerow(self._row(sample))
                count += 1
                if count % self.resource_policy.flush_every_samples == 0:
                    f.flush()
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
            f.flush()
        return count


    @staticmethod
    def _write_contract_compatibility_artifacts(
        evidence: AttemptEvidence,
        required: tuple[str, ...],
        canonical: dict[str, Any],
        initial_sample: Any,
        module_result: dict[str, Any],
    ) -> None:
        """Bridge legacy evidence contracts into the generic runtime.

        NAV-001 predates the generic capability runner and intentionally keeps
        three historical evidence files in its frozen contract.  The generic
        lifecycle already performs equivalent input validation and precondition
        gating, so record those outcomes without weakening or rewriting the
        frozen specification.
        """
        required_set = set(required)
        lifecycle = set(module_result.get("lifecycle") or [])
        reason = module_result.get("reason")
        error_code = module_result.get("error_code")

        input_valid = "CHECK_PRECONDITIONS" in lifecycle
        boundary_passed = "EXECUTE" in lifecycle

        if (
            "input_validation.json" in required_set
            and not (evidence.path / "input_validation.json").exists()
        ):
            evidence.write_json(
                "input_validation.json",
                {
                    "valid": input_valid,
                    "failure_code": None if input_valid else (error_code or "INVALID_INPUT"),
                    "errors": [] if input_valid else ([str(reason)] if reason else []),
                    "source": "generic_capability_lifecycle",
                },
            )

        if (
            "boundary_result.json" in required_set
            and not (evidence.path / "boundary_result.json").exists()
        ):
            boundary = {
                "overall": "PASS" if boundary_passed else "FAIL",
                "scope": "SIMULATION_ONLY",
                "reason": (
                    "generic_capability_preconditions_passed"
                    if boundary_passed
                    else (str(reason) if reason else "generic_capability_preconditions_failed")
                ),
                "start_position_m": [
                    float(initial_sample.x),
                    float(initial_sample.y),
                    float(initial_sample.z),
                ],
            }
            if "target_position_m" in canonical:
                boundary["target_position_m"] = list(canonical["target_position_m"])
            evidence.write_json("boundary_result.json", boundary)

        if (
            "unit_test_result.json" in required_set
            and not (evidence.path / "unit_test_result.json").exists()
        ):
            evidence.write_json(
                "unit_test_result.json",
                {
                    "status": "NOT_RUN_IN_RUNTIME_EXPERIMENT",
                    "note": (
                        "Project unit suite is a pre-experiment gate; "
                        "the generic runtime does not execute or mutate it."
                    ),
                },
            )

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
        except Exception as exc:  # cleanup must not mask evidence/verdict
            return f"{type(exc).__name__}: {exc}"

    async def _post_action(self, adapter: Any, action: str) -> str | None:
        action = action.strip().lower()
        if action in {"", "none"}:
            return None
        try:
            if action == "hold":
                hold = getattr(adapter, "hold_position")
                await hold()
                return None
            if action == "land":
                land = getattr(adapter, "land_and_wait")
                await land(timeout_s=30.0)
                return None
            raise ValueError(f"unsupported post_action: {action}")
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    async def run(
        self,
        capability_id: str,
        *,
        input_data: dict[str, Any],
        scenario_id: str,
        adapter: Any,
        post_action: str = "none",
        close_adapter: bool = True,
    ) -> CapabilityExperimentResult:
        definition = self.registry.get_definition(capability_id)
        contract = EvidenceContractValidator(definition.spec_path(self.project))
        timeout = float(input_data.get("timeout_s", 0))
        self.resources.assert_can_start(timeout)

        attempt_number = next_attempt_number(
            runs_root=self.runs_root,
            task_id=capability_id,
            scenario_id=scenario_id,
        )
        evidence = AttemptEvidence(
            root=self.runs_root,
            task_id=capability_id,
            scenario_id=scenario_id,
            attempt=attempt_number,
        )
        attempt = evidence.create()
        run_id = datetime.now(timezone.utc).strftime(
            capability_id.replace("-", "") + "-%Y%m%dT%H%M%S-%fZ"
        )
        canonical = dict(input_data)
        canonical.setdefault("task_id", capability_id)
        canonical.setdefault("scenario_id", scenario_id)

        evidence.write_json("raw_user_input.json", dict(input_data))
        evidence.write_json("input.json", canonical)
        evidence.write_json(
            "changed_files.json",
            {
                "changed_files": [],
                "reason": "runtime_experiment_no_code_mutation",
            },
        )
        evidence.write_metadata(
            {
                "run_id": run_id,
                "capability_id": capability_id,
                "scope": "PX4_SITL_SIMULATION_ONLY",
                "verdict_authority": "independent_python_verifier",
                "execution_telemetry_scope": "capability_window_only",
            }
        )
        evidence.append_event("capability_experiment_started")

        telemetry_path = attempt / "telemetry.csv"
        recorder_task: asyncio.Task[int] | None = None
        stop_event = asyncio.Event()
        module_result_dict: dict[str, Any] = {
            "status": "not_started",
            "reason": "runtime_setup_not_completed",
        }
        verification_dict: dict[str, Any] = {
            "overall": "FAIL",
            "reason": "verification_not_run",
        }
        post_action_error: str | None = None
        post_action_attempted = False
        module_completed_successfully = False
        adapter_cleanup_error: str | None = None
        initial_sample: Any | None = None

        try:
            await adapter.connect()
            initial_sample = await adapter.get_telemetry_sample()
            self._write_header_and_sample(telemetry_path, initial_sample)
            evidence.append_event(
                "initial_telemetry_captured",
                {
                    "vehicle_state": initial_sample.vehicle_state,
                    "localization_valid": initial_sample.localization_valid,
                    "position_m": [initial_sample.x, initial_sample.y, initial_sample.z],
                },
            )

            recorder_task = asyncio.create_task(
                self._record_until_stop(adapter, telemetry_path, stop_event)
            )

            lifecycle_events: list[dict[str, Any]] = []
            result = await self.executor.execute(
                capability_id,
                adapter=adapter,
                input_data=canonical,
                log_hook=lambda state_value, context: lifecycle_events.append(
                    {"state": state_value.value, "context": context}
                ),
            )
            module_result_dict = result.to_dict()
            module_completed_successfully = result.status_value == "completed"
            evidence.write_json("module_output.json", module_result_dict)
            evidence.write_json("lifecycle.json", {"events": lifecycle_events})
            evidence.append_event(
                "module_completed",
                {
                    "status": result.status_value,
                    "error_code": result.error_code,
                },
            )
            self._write_contract_compatibility_artifacts(
                evidence,
                contract.required,
                canonical,
                initial_sample,
                module_result_dict,
            )

            stop_event.set()
            if recorder_task is not None:
                await recorder_task
                recorder_task = None

            # Capture one explicit terminal sample after the module finishes so
            # the verifier's final row is always a measured post-capability
            # state rather than a scheduler-dependent background sample.
            terminal_sample = await adapter.get_telemetry_sample()
            self._append_sample(telemetry_path, terminal_sample)
            evidence.append_event("execution_telemetry_frozen")

            verification = self.executor.verify_attempt(
                capability_id,
                attempt,
                write_result=True,
            )
            verification_dict = verification.to_dict()
            evidence.append_event("verification_completed", verification_dict)

            contract_result = contract.validate(attempt)
            evidence.write_json(
                "evidence_contract_result.json",
                contract_result.to_dict(),
            )
            if not contract_result.complete:
                raise RuntimeError(
                    "evidence contract incomplete: "
                    + ",".join(contract_result.missing)
                )
            evidence.finalize()

            post_action_attempted = True
            post_action_error = await self._post_action(adapter, post_action)

        except Exception as exc:
            stop_event.set()
            if recorder_task is not None:
                try:
                    await recorder_task
                except Exception:
                    pass
                recorder_task = None

            if not telemetry_path.exists():
                self._write_header_only(telemetry_path)

            # A completed flight capability must not be left unstabilized only
            # because verification/evidence finalization failed afterward. Honor
            # the caller's requested post-action on this post-execution error
            # path before closing the MAVSDK adapter.
            if module_completed_successfully and not post_action_attempted:
                post_action_attempted = True
                post_action_error = await self._post_action(adapter, post_action)

            if not evidence.is_finalized:
                try:
                    evidence.write_json(
                        "runtime_error.json",
                        {"type": type(exc).__name__, "message": str(exc)},
                    )
                    if not (attempt / "module_output.json").exists():
                        evidence.write_json("module_output.json", module_result_dict)
                    if not (attempt / "lifecycle.json").exists():
                        evidence.write_json("lifecycle.json", {"events": []})
                    if not (attempt / "verification.json").exists():
                        try:
                            verification = self.executor.verify_attempt(
                                capability_id,
                                attempt,
                                write_result=True,
                            )
                            verification_dict = verification.to_dict()
                        except Exception as verifier_exc:
                            verification_dict = {
                                "overall": "FAIL",
                                "reason": f"verifier_exception:{verifier_exc}",
                            }
                            evidence.write_json(
                                "verification.json",
                                verification_dict,
                            )
                    evidence.append_event(
                        "capability_experiment_failed",
                        {"error": f"{type(exc).__name__}: {exc}"},
                    )
                    if initial_sample is not None:
                        self._write_contract_compatibility_artifacts(
                            evidence,
                            contract.required,
                            canonical,
                            initial_sample,
                            module_result_dict,
                        )
                    contract_result = contract.validate(attempt)
                    evidence.write_json(
                        "evidence_contract_result.json",
                        contract_result.to_dict(),
                    )
                    if contract_result.complete:
                        evidence.finalize()
                except Exception:
                    pass
            raise
        finally:
            if close_adapter:
                adapter_cleanup_error = await self._close_adapter(adapter)

        return CapabilityExperimentResult(
            run_id=run_id,
            capability_id=capability_id,
            attempt_dir=attempt,
            module_result=module_result_dict,
            verification=verification_dict,
            evidence_complete=True,
            adapter_cleanup_error=adapter_cleanup_error,
            post_action_error=post_action_error,
        )
