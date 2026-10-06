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
from ai.validators.battery_energy import BATTERY_ENERGY_CALCULATION_KIND


RESERVE_VALIDATOR_CONTRACT_VERSION = "1.0"
RESERVE_VALIDATOR_ID = "reserve"
RESERVE_CALCULATION_KIND = "reserve"
MAX_RESERVE_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES

_BATTERY_KEYS = frozenset({"kind", "voltage_v", "capacity_ah", "reported_energy_wh"})
_RESERVE_KEYS = frozenset(
    {
        "kind",
        "reserve_percent",
        "reported_reserve_wh",
        "reported_usable_energy_wh",
    }
)


class ReserveValidationError(RuntimeError):
    """Base error for V7.6 deterministic reserve validation."""


class ReserveValidationConflictError(ReserveValidationError):
    """Raised when V7.6 cannot bind safely to persisted project state."""


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_RESERVE_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_RESERVE_VALIDATION_BYTES")

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


def _decimal_nonnegative(value: Any, *, field: str) -> Decimal:
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


def _extract_one(
    payload: ResultDraftArtifactPayload, *, kind: str, label: str
) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for item in payload.result.to_dict()["calculations"]:
        if isinstance(item, Mapping) and item.get("kind") == kind:
            matches.append(item)
    if not matches:
        raise ValueError(f"result.calculations must contain one {label} calculation")
    if len(matches) != 1:
        raise ValueError(f"result.calculations must contain exactly one {label} calculation")
    return matches[0]


def _evaluate_battery_energy(energy: Mapping[str, Any]) -> Decimal:
    if frozenset(energy.keys()) != _BATTERY_KEYS:
        raise ValueError(
            "battery_energy calculation must contain exactly "
            "kind,voltage_v,capacity_ah,reported_energy_wh"
        )
    if energy["kind"] != BATTERY_ENERGY_CALCULATION_KIND:
        raise ValueError("battery_energy kind mismatch")

    voltage = _decimal_nonnegative(energy["voltage_v"], field="battery_energy.voltage_v")
    capacity = _decimal_nonnegative(energy["capacity_ah"], field="battery_energy.capacity_ah")
    reported = _decimal_nonnegative(
        energy["reported_energy_wh"], field="battery_energy.reported_energy_wh"
    )
    computed = voltage * capacity
    if not computed.is_finite():
        raise ValueError("computed battery energy must be finite")
    if computed != reported:
        raise ValueError(
            "battery_energy.reported_energy_wh does not equal deterministic V * Ah product"
        )
    return computed


def _evaluate_reserve(
    reserve: Mapping[str, Any], *, battery_energy_wh: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    if frozenset(reserve.keys()) != _RESERVE_KEYS:
        raise ValueError(
            "reserve calculation must contain exactly "
            "kind,reserve_percent,reported_reserve_wh,reported_usable_energy_wh"
        )
    if reserve["kind"] != RESERVE_CALCULATION_KIND:
        raise ValueError("reserve kind mismatch")

    percent = _decimal_nonnegative(reserve["reserve_percent"], field="reserve.reserve_percent")
    if percent > Decimal("100"):
        raise ValueError("reserve.reserve_percent must be in 0..100")

    reported_reserve = _decimal_nonnegative(
        reserve["reported_reserve_wh"], field="reserve.reported_reserve_wh"
    )
    reported_usable = _decimal_nonnegative(
        reserve["reported_usable_energy_wh"], field="reserve.reported_usable_energy_wh"
    )

    computed_reserve = battery_energy_wh * percent / Decimal("100")
    computed_usable = battery_energy_wh - computed_reserve
    if not computed_reserve.is_finite() or not computed_usable.is_finite():
        raise ValueError("computed reserve values must be finite")

    return percent, computed_reserve, reported_reserve, computed_usable, reported_usable


def _detail_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("reserve evidence cannot be represented as finite JSON number")
    return number


def _check(
    *,
    artifact_type: str,
    battery_energy_wh: Decimal,
    percent: Decimal,
    computed_reserve: Decimal,
    reported_reserve: Decimal,
    computed_usable: Decimal,
    reported_usable: Decimal,
) -> ValidationCheck:
    reserve_reconciled = computed_reserve == reported_reserve
    usable_reconciled = computed_usable == reported_usable

    if not reserve_reconciled:
        status = ValidationStatus.FAIL
        reason = "reported_reserve_wh does not equal deterministic battery_wh * reserve_percent / 100"
    elif not usable_reconciled:
        status = ValidationStatus.FAIL
        reason = "reported_usable_energy_wh does not equal battery_wh - deterministic reserve_wh"
    else:
        status = ValidationStatus.PASS
        reason = None

    return ValidationCheck.from_dict(
        {
            "check_id": RESERVE_VALIDATOR_ID,
            "status": status.value,
            "field": "result.calculations.reserve",
            "reason": reason,
            "details": {
                "validator_contract_version": RESERVE_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "energy_unit": "Wh",
                "percent_unit": "%",
                "reserve_formula": "reserve_wh=battery_wh*reserve_percent/100",
                "usable_formula": "usable_wh=battery_wh-reserve_wh",
                "battery_energy_wh": _detail_number(battery_energy_wh),
                "reserve_percent": _detail_number(percent),
                "computed_reserve_wh": _detail_number(computed_reserve),
                "reported_reserve_wh": _detail_number(reported_reserve),
                "computed_usable_energy_wh": _detail_number(computed_usable),
                "reported_usable_energy_wh": _detail_number(reported_usable),
                "battery_energy_reconciled": True,
                "reserve_reconciled": reserve_reconciled,
                "usable_reconciled": usable_reconciled,
            },
        }
    )


def _failure(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": RESERVE_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "result.calculations.reserve",
            "reason": reason,
            "details": {
                "validator_contract_version": RESERVE_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "energy_unit": "Wh",
                "percent_unit": "%",
                "reserve_formula": "reserve_wh=battery_wh*reserve_percent/100",
                "usable_formula": "usable_wh=battery_wh-reserve_wh",
            },
        }
    )


class DeterministicReserveValidator:
    """V7.6 deterministic reserve-energy calculator/validator.

    V7.6 binds one reconciled ``battery_energy`` calculation to one ``reserve``
    calculation in the current G6.8 ``ai.agent_result.v1`` artifact. It derives
    reserve and usable nominal energy with Decimal bookkeeping and verifies the
    reported results exactly. It returns C1.7 ValidationResult only; it does not
    persist validation evidence or alter artifact/task lifecycle.
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
            raise ReserveValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ReserveValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if RESERVE_VALIDATOR_ID not in task.validators:
            raise ReserveValidationConflictError(
                "producer task did not request the reserve validator"
            )

        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason="reserve validator currently requires ai.agent_result.v1",
                    ),
                )
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise ReserveValidationConflictError(
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

            battery = _extract_one(
                payload, kind=BATTERY_ENERGY_CALCULATION_KIND, label="battery_energy"
            )
            reserve = _extract_one(payload, kind=RESERVE_CALCULATION_KIND, label="reserve")
            battery_energy_wh = _evaluate_battery_energy(battery)
            percent, computed_reserve, reported_reserve, computed_usable, reported_usable = (
                _evaluate_reserve(reserve, battery_energy_wh=battery_energy_wh)
            )
            check = _check(
                artifact_type=record.type,
                battery_energy_wh=battery_energy_wh,
                percent=percent,
                computed_reserve=computed_reserve,
                reported_reserve=reported_reserve,
                computed_usable=computed_usable,
                reported_usable=reported_usable,
            )
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            check = _failure(
                artifact_type=record.type,
                reason=f"reserve validation failed: {exc}",
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise ReserveValidationConflictError(
                "reserve validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks((check,))
