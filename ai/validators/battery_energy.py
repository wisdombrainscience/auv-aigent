from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import math
from typing import Any, Mapping

from ai.agents.draft_artifact import (
    MAX_RESULT_DRAFT_ARTIFACT_BYTES,
    RESULT_DRAFT_ARTIFACT_TYPE,
    ResultDraftArtifactConflictError,
    ResultDraftArtifactPayload,
)
from ai.artifacts.hashing import ArtifactContentStore, ArtifactHashError
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.ids import CanonicalId
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project.store import ProjectStore
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


BATTERY_ENERGY_VALIDATOR_CONTRACT_VERSION = "1.0"
BATTERY_ENERGY_VALIDATOR_ID = "battery_energy"
BATTERY_ENERGY_CALCULATION_KIND = "battery_energy"
MAX_BATTERY_ENERGY_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES

_ENERGY_KEYS = frozenset({"kind", "voltage_v", "capacity_ah", "reported_energy_wh"})


class BatteryEnergyValidationError(RuntimeError):
    """Base error for V7.5 deterministic battery-energy validation."""


class BatteryEnergyValidationConflictError(BatteryEnergyValidationError):
    """Raised when V7.5 cannot bind safely to persisted project state."""


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_BATTERY_ENERGY_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_BATTERY_ENERGY_VALIDATION_BYTES")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value!r}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("artifact content is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("artifact content is invalid JSON") from exc

    if not isinstance(parsed, Mapping):
        raise ValueError("artifact content top level must be an object")
    return parsed


def _decimal_number(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not a valid decimal number") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _extract_energy(payload: ResultDraftArtifactPayload) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for item in payload.result.to_dict()["calculations"]:
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") == BATTERY_ENERGY_CALCULATION_KIND:
            matches.append(item)
    if not matches:
        raise ValueError("result.calculations must contain one battery_energy calculation")
    if len(matches) != 1:
        raise ValueError("result.calculations must contain exactly one battery_energy calculation")
    return matches[0]


def _evaluate_energy(energy: Mapping[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if frozenset(energy.keys()) != _ENERGY_KEYS:
        raise ValueError(
            "battery_energy calculation must contain exactly "
            "kind,voltage_v,capacity_ah,reported_energy_wh"
        )
    if energy["kind"] != BATTERY_ENERGY_CALCULATION_KIND:
        raise ValueError("battery_energy kind mismatch")

    voltage = _decimal_number(energy["voltage_v"], field="battery_energy.voltage_v")
    capacity = _decimal_number(energy["capacity_ah"], field="battery_energy.capacity_ah")
    reported = _decimal_number(
        energy["reported_energy_wh"], field="battery_energy.reported_energy_wh"
    )
    computed = voltage * capacity
    if not computed.is_finite():
        raise ValueError("computed battery energy must be finite")
    return voltage, capacity, computed, reported


def _detail_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("battery-energy evidence cannot be represented as finite JSON number")
    return number


def _check(
    *,
    artifact_type: str,
    voltage: Decimal,
    capacity: Decimal,
    computed: Decimal,
    reported: Decimal,
) -> ValidationCheck:
    reconciled = computed == reported
    status = ValidationStatus.PASS if reconciled else ValidationStatus.FAIL
    reason = None if reconciled else "reported_energy_wh does not equal deterministic V * Ah product"
    return ValidationCheck.from_dict(
        {
            "check_id": BATTERY_ENERGY_VALIDATOR_ID,
            "status": status.value,
            "field": "result.calculations.battery_energy",
            "reason": reason,
            "details": {
                "validator_contract_version": BATTERY_ENERGY_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "voltage_unit": "V",
                "capacity_unit": "Ah",
                "energy_unit": "Wh",
                "formula": "Wh=V*Ah",
                "voltage_v": _detail_number(voltage),
                "capacity_ah": _detail_number(capacity),
                "computed_energy_wh": _detail_number(computed),
                "reported_energy_wh": _detail_number(reported),
                "reconciled": reconciled,
            },
        }
    )


def _failure(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": BATTERY_ENERGY_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "result.calculations.battery_energy",
            "reason": reason,
            "details": {
                "validator_contract_version": BATTERY_ENERGY_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "voltage_unit": "V",
                "capacity_unit": "Ah",
                "energy_unit": "Wh",
                "formula": "Wh=V*Ah",
            },
        }
    )


class DeterministicBatteryEnergyValidator:
    """V7.5 deterministic battery-energy calculator/validator.

    V7.5 operates on the current G6.8 ``ai.agent_result.v1`` artifact and one
    explicitly tagged ``battery_energy`` calculation. It recomputes energy from
    canonical volts and ampere-hours using Decimal bookkeeping and verifies the
    declared watt-hour result exactly. It returns C1.7 ValidationResult only;
    it does not persist validation evidence or alter artifact/task lifecycle.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._versions = ArtifactVersionStore(project_store)
        self._content = ArtifactContentStore(project_store)
        self._tasks = TaskStore(project_store)

    def validate(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> ValidationResult:
        try:
            record = self._versions.open_version(project_id, artifact_id, version)
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise BatteryEnergyValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise BatteryEnergyValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if BATTERY_ENERGY_VALIDATOR_ID not in task.validators:
            raise BatteryEnergyValidationConflictError(
                "producer task did not request the battery_energy validator"
            )

        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason="battery_energy validator currently requires ai.agent_result.v1",
                    ),
                )
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise BatteryEnergyValidationConflictError(
                "artifact content could not be read safely"
            ) from exc

        try:
            decoded = _strict_json_object(raw)
            payload = ResultDraftArtifactPayload.from_dict(decoded)
            if payload.project_id != record.project_id:
                raise ValueError("payload project_id does not match artifact project")
            if payload.task_id != record.producer_task:
                raise ValueError("payload task_id does not match artifact producer_task")
            if payload.role_id != task.role:
                raise ValueError("payload role_id does not match producer task role")
            if payload.schema_id != task.output_schema:
                raise ValueError("payload schema_id does not match producer task output_schema")
            energy = _extract_energy(payload)
            voltage, capacity, computed, reported = _evaluate_energy(energy)
            check = _check(
                artifact_type=record.type,
                voltage=voltage,
                capacity=capacity,
                computed=computed,
                reported=reported,
            )
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            check = _failure(
                artifact_type=record.type,
                reason=f"battery energy validation failed: {exc}",
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise BatteryEnergyValidationConflictError(
                "battery energy validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks((check,))
