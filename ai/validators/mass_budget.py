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


MASS_BUDGET_VALIDATOR_CONTRACT_VERSION = "1.0"
MASS_BUDGET_VALIDATOR_ID = "mass_budget"
MASS_BUDGET_CALCULATION_KIND = "mass_budget"
MAX_MASS_BUDGET_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES
MAX_MASS_BUDGET_COMPONENTS = 128
MAX_MASS_COMPONENT_NAME_CHARS = 128

_BUDGET_KEYS = frozenset({"kind", "components", "reported_total_kg", "limit_kg"})
_COMPONENT_KEYS = frozenset({"name", "mass_kg"})


class MassBudgetValidationError(RuntimeError):
    """Base error for V7.4 deterministic mass-budget validation."""


class MassBudgetValidationConflictError(MassBudgetValidationError):
    """Raised when V7.4 cannot bind safely to persisted project state."""


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_MASS_BUDGET_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_MASS_BUDGET_VALIDATION_BYTES")

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


def _clean_name(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be clean non-empty text")
    if len(value) > MAX_MASS_COMPONENT_NAME_CHARS:
        raise ValueError(f"{field} exceeds MAX_MASS_COMPONENT_NAME_CHARS")
    return value


def _decimal_mass(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not a valid decimal mass") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _extract_budget(payload: ResultDraftArtifactPayload) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for index, item in enumerate(payload.result.to_dict()["calculations"]):
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") == MASS_BUDGET_CALCULATION_KIND:
            matches.append(item)
    if not matches:
        raise ValueError("result.calculations must contain one mass_budget calculation")
    if len(matches) != 1:
        raise ValueError("result.calculations must contain exactly one mass_budget calculation")
    return matches[0]


def _evaluate_budget(budget: Mapping[str, Any]) -> tuple[int, Decimal, Decimal, Decimal]:
    if frozenset(budget.keys()) != _BUDGET_KEYS:
        raise ValueError(
            "mass_budget calculation must contain exactly "
            "kind,components,reported_total_kg,limit_kg"
        )
    if budget["kind"] != MASS_BUDGET_CALCULATION_KIND:
        raise ValueError("mass_budget kind mismatch")

    components = budget["components"]
    if not isinstance(components, list):
        raise ValueError("mass_budget.components must be an array")
    if not components:
        raise ValueError("mass_budget.components must not be empty")
    if len(components) > MAX_MASS_BUDGET_COMPONENTS:
        raise ValueError("mass_budget.components exceeds MAX_MASS_BUDGET_COMPONENTS")

    total = Decimal("0")
    seen: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, Mapping) or frozenset(component.keys()) != _COMPONENT_KEYS:
            raise ValueError(
                f"mass_budget.components[{index}] must contain exactly name,mass_kg"
            )
        name = _clean_name(component["name"], field=f"mass_budget.components[{index}].name")
        if name in seen:
            raise ValueError(f"duplicate mass component name {name!r}")
        seen.add(name)
        total += _decimal_mass(
            component["mass_kg"], field=f"mass_budget.components[{index}].mass_kg"
        )

    reported = _decimal_mass(budget["reported_total_kg"], field="mass_budget.reported_total_kg")
    limit = _decimal_mass(budget["limit_kg"], field="mass_budget.limit_kg")
    return len(components), total, reported, limit


def _detail_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        integer = int(value)
        # C1.7 accepts arbitrary JSON integers; keep exact integral totals exact.
        return integer
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("mass-budget evidence cannot be represented as finite JSON number")
    return number


def _check(
    *,
    artifact_type: str,
    component_count: int,
    computed: Decimal,
    reported: Decimal,
    limit: Decimal,
) -> ValidationCheck:
    reconciled = computed == reported
    within_limit = computed <= limit
    if not reconciled:
        reason = "reported_total_kg does not equal deterministic component sum"
        status = ValidationStatus.FAIL
    elif not within_limit:
        reason = "deterministic mass total exceeds limit_kg"
        status = ValidationStatus.FAIL
    else:
        reason = None
        status = ValidationStatus.PASS

    return ValidationCheck.from_dict(
        {
            "check_id": MASS_BUDGET_VALIDATOR_ID,
            "status": status.value,
            "field": "result.calculations.mass_budget",
            "reason": reason,
            "details": {
                "validator_contract_version": MASS_BUDGET_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "unit": "kg",
                "component_count": component_count,
                "computed_total_kg": _detail_number(computed),
                "reported_total_kg": _detail_number(reported),
                "limit_kg": _detail_number(limit),
                "reconciled": reconciled,
                "within_limit": within_limit,
            },
        }
    )


def _failure(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": MASS_BUDGET_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "result.calculations.mass_budget",
            "reason": reason,
            "details": {
                "validator_contract_version": MASS_BUDGET_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "unit": "kg",
            },
        }
    )


class DeterministicMassBudgetValidator:
    """V7.4 deterministic mass-budget calculator/validator.

    V7.4 operates on the current G6.8 ``ai.agent_result.v1`` artifact and one
    explicitly tagged ``mass_budget`` calculation. It recomputes the declared
    component sum in canonical kilograms and checks it against the declared
    total and limit. It returns C1.7 ValidationResult only; it does not persist
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
            raise MassBudgetValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise MassBudgetValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if MASS_BUDGET_VALIDATOR_ID not in task.validators:
            raise MassBudgetValidationConflictError(
                "producer task did not request the mass_budget validator"
            )

        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason="mass_budget validator currently requires ai.agent_result.v1",
                    ),
                )
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise MassBudgetValidationConflictError(
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
            budget = _extract_budget(payload)
            component_count, computed, reported, limit = _evaluate_budget(budget)
            check = _check(
                artifact_type=record.type,
                component_count=component_count,
                computed=computed,
                reported=reported,
                limit=limit,
            )
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            check = _failure(
                artifact_type=record.type,
                reason=f"mass budget validation failed: {exc}",
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise MassBudgetValidationConflictError(
                "mass budget validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks((check,))
