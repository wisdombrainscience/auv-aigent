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


THRUST_TO_WEIGHT_VALIDATOR_CONTRACT_VERSION = "1.0"
THRUST_TO_WEIGHT_VALIDATOR_ID = "thrust_to_weight"
THRUST_TO_WEIGHT_CALCULATION_KIND = "thrust_to_weight"
MAX_THRUST_TO_WEIGHT_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES

_THRUST_TO_WEIGHT_KEYS = frozenset(
    {
        "kind",
        "total_thrust_n",
        "mass_kg",
        "gravity_m_s2",
        "reported_thrust_to_weight",
    }
)


class ThrustToWeightValidationError(RuntimeError):
    """Base error for V7.7 deterministic thrust-to-weight validation."""


class ThrustToWeightValidationConflictError(ThrustToWeightValidationError):
    """Raised when V7.7 cannot bind safely to persisted project state."""


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_THRUST_TO_WEIGHT_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_THRUST_TO_WEIGHT_VALIDATION_BYTES")

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


def _extract_calculation(payload: ResultDraftArtifactPayload) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for item in payload.result.to_dict()["calculations"]:
        if isinstance(item, Mapping) and item.get("kind") == THRUST_TO_WEIGHT_CALCULATION_KIND:
            matches.append(item)
    if not matches:
        raise ValueError("result.calculations must contain one thrust_to_weight calculation")
    if len(matches) != 1:
        raise ValueError("result.calculations must contain exactly one thrust_to_weight calculation")
    return matches[0]


def _evaluate(
    calculation: Mapping[str, Any],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, bool]:
    if frozenset(calculation.keys()) != _THRUST_TO_WEIGHT_KEYS:
        raise ValueError(
            "thrust_to_weight calculation must contain exactly "
            "kind,total_thrust_n,mass_kg,gravity_m_s2,reported_thrust_to_weight"
        )
    if calculation["kind"] != THRUST_TO_WEIGHT_CALCULATION_KIND:
        raise ValueError("thrust_to_weight kind mismatch")

    thrust = _decimal_nonnegative(
        calculation["total_thrust_n"], field="thrust_to_weight.total_thrust_n"
    )
    mass = _decimal_nonnegative(
        calculation["mass_kg"], field="thrust_to_weight.mass_kg"
    )
    gravity = _decimal_nonnegative(
        calculation["gravity_m_s2"], field="thrust_to_weight.gravity_m_s2"
    )
    reported = _decimal_nonnegative(
        calculation["reported_thrust_to_weight"],
        field="thrust_to_weight.reported_thrust_to_weight",
    )

    if mass <= 0:
        raise ValueError("thrust_to_weight.mass_kg must be greater than zero")
    if gravity <= 0:
        raise ValueError("thrust_to_weight.gravity_m_s2 must be greater than zero")

    weight = mass * gravity
    if not weight.is_finite() or weight <= 0:
        raise ValueError("computed weight must be finite and greater than zero")

    # Reconciliation uses exact decimal cross-multiplication rather than
    # floating-point division, so reported ratios with finite decimal notation
    # are checked deterministically without hidden rounding policy.
    expected_thrust = reported * weight
    if not expected_thrust.is_finite():
        raise ValueError("computed thrust-to-weight reconciliation must be finite")
    reconciled = expected_thrust == thrust

    # Evidence ratio is only emitted after the algebraic comparison.  Decimal
    # division is deterministic for display evidence but is not authoritative
    # for PASS/FAIL.
    ratio = thrust / weight
    if not ratio.is_finite():
        raise ValueError("computed thrust-to-weight ratio must be finite")

    return thrust, mass, gravity, weight, ratio, reconciled


def _detail_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("thrust-to-weight evidence cannot be represented as finite JSON number")
    return number


def _check(
    *,
    artifact_type: str,
    thrust: Decimal,
    mass: Decimal,
    gravity: Decimal,
    weight: Decimal,
    ratio: Decimal,
    reported: Decimal,
    reconciled: bool,
) -> ValidationCheck:
    status = ValidationStatus.PASS if reconciled else ValidationStatus.FAIL
    reason = (
        None
        if reconciled
        else "reported_thrust_to_weight does not reconcile with total_thrust_n / (mass_kg * gravity_m_s2)"
    )
    return ValidationCheck.from_dict(
        {
            "check_id": THRUST_TO_WEIGHT_VALIDATOR_ID,
            "status": status.value,
            "field": "result.calculations.thrust_to_weight",
            "reason": reason,
            "details": {
                "validator_contract_version": THRUST_TO_WEIGHT_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "thrust_unit": "N",
                "mass_unit": "kg",
                "gravity_unit": "m/s^2",
                "ratio_unit": "1",
                "formula": "T/W=total_thrust_n/(mass_kg*gravity_m_s2)",
                "total_thrust_n": _detail_number(thrust),
                "mass_kg": _detail_number(mass),
                "gravity_m_s2": _detail_number(gravity),
                "computed_weight_n": _detail_number(weight),
                "computed_thrust_to_weight": _detail_number(ratio),
                "reported_thrust_to_weight": _detail_number(reported),
                "reconciled": reconciled,
            },
        }
    )


def _failure(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": THRUST_TO_WEIGHT_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "result.calculations.thrust_to_weight",
            "reason": reason,
            "details": {
                "validator_contract_version": THRUST_TO_WEIGHT_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "thrust_unit": "N",
                "mass_unit": "kg",
                "gravity_unit": "m/s^2",
                "ratio_unit": "1",
                "formula": "T/W=total_thrust_n/(mass_kg*gravity_m_s2)",
            },
        }
    )


class DeterministicThrustToWeightValidator:
    """V7.7 deterministic thrust-to-weight calculator/validator.

    V7.7 validates one explicitly tagged ``thrust_to_weight`` calculation in
    the current G6.8 ``ai.agent_result.v1`` artifact.  It derives weight from
    canonical kilograms and metres per second squared, then reconciles the
    reported dimensionless ratio against canonical newtons using exact Decimal
    algebra.  It returns C1.7 ValidationResult only and does not persist
    validation evidence or alter artifact/task lifecycle.
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
            raise ThrustToWeightValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ThrustToWeightValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if THRUST_TO_WEIGHT_VALIDATOR_ID not in task.validators:
            raise ThrustToWeightValidationConflictError(
                "producer task did not request the thrust_to_weight validator"
            )

        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason="thrust_to_weight validator currently requires ai.agent_result.v1",
                    ),
                )
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise ThrustToWeightValidationConflictError(
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

            calculation = _extract_calculation(payload)
            thrust, mass, gravity, weight, ratio, reconciled = _evaluate(calculation)
            reported = _decimal_nonnegative(
                calculation["reported_thrust_to_weight"],
                field="thrust_to_weight.reported_thrust_to_weight",
            )
            check = _check(
                artifact_type=record.type,
                thrust=thrust,
                mass=mass,
                gravity=gravity,
                weight=weight,
                ratio=ratio,
                reported=reported,
                reconciled=reconciled,
            )
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            check = _failure(
                artifact_type=record.type,
                reason=f"thrust_to_weight validation failed: {exc}",
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise ThrustToWeightValidationConflictError(
                "thrust_to_weight validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks((check,))
