from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import re
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
from ai.validators.battery_energy import DeterministicBatteryEnergyValidator
from ai.validators.mass_budget import DeterministicMassBudgetValidator
from ai.validators.mission_distance import DeterministicMissionDistanceValidator
from ai.validators.reserve import DeterministicReserveValidator
from ai.validators.thrust_to_weight import DeterministicThrustToWeightValidator


CONSTRAINT_FRAMEWORK_CONTRACT_VERSION = "1.0"
CONSTRAINT_FRAMEWORK_VALIDATOR_ID = "constraints"
CONSTRAINT_CALCULATION_KIND = "constraints"
MAX_CONSTRAINT_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES
MAX_CONSTRAINTS = 64
MAX_CONSTRAINT_ID_CHARS = 64
MAX_CONSTRAINT_FIELD_CHARS = 128
CONSTRAINT_OPERATORS = ("<", "<=", "==", "!=", ">=", ">")

_CONSTRAINT_SET_KEYS = frozenset({"kind", "constraints"})
_CONSTRAINT_KEYS = frozenset(
    {
        "constraint_id",
        "field",
        "source_kind",
        "source_field",
        "operator",
        "required_value",
        "unit",
    }
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ConstraintFrameworkValidationError(RuntimeError):
    """Base error for V7.9 deterministic constraint validation."""


class ConstraintFrameworkValidationConflictError(ConstraintFrameworkValidationError):
    """Raised when V7.9 cannot bind safely to persisted project state."""


@dataclass(frozen=True)
class _SourceSpec:
    validator_id: str
    validator_type: type
    units: Mapping[str, str]


_SOURCE_SPECS: dict[str, _SourceSpec] = {
    "mass_budget": _SourceSpec(
        validator_id="mass_budget",
        validator_type=DeterministicMassBudgetValidator,
        units={
            "reported_total_kg": "kg",
            "limit_kg": "kg",
        },
    ),
    "battery_energy": _SourceSpec(
        validator_id="battery_energy",
        validator_type=DeterministicBatteryEnergyValidator,
        units={
            "voltage_v": "V",
            "capacity_ah": "Ah",
            "reported_energy_wh": "Wh",
        },
    ),
    "reserve": _SourceSpec(
        validator_id="reserve",
        validator_type=DeterministicReserveValidator,
        units={
            "reserve_percent": "%",
            "reported_reserve_wh": "Wh",
            "reported_usable_energy_wh": "Wh",
        },
    ),
    "thrust_to_weight": _SourceSpec(
        validator_id="thrust_to_weight",
        validator_type=DeterministicThrustToWeightValidator,
        units={
            "total_thrust_n": "N",
            "mass_kg": "kg",
            "gravity_m_s2": "m/s^2",
            "reported_thrust_to_weight": "1",
        },
    ),
    "mission_distance": _SourceSpec(
        validator_id="mission_distance",
        validator_type=DeterministicMissionDistanceValidator,
        units={
            "reported_distance_m": "m",
        },
    ),
}
SUPPORTED_CONSTRAINT_SOURCES = tuple(sorted(_SOURCE_SPECS))


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_CONSTRAINT_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_CONSTRAINT_VALIDATION_BYTES")

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


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be clean non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds maximum length {maximum}")
    return value


def _constraint_id(value: Any, *, field: str) -> str:
    text = _clean_text(value, field=field, maximum=MAX_CONSTRAINT_ID_CHARS)
    if not _ID_RE.fullmatch(text):
        raise ValueError(
            f"{field} must use only ASCII letters, digits, underscore, dot, or hyphen"
        )
    return text


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
    return number


def _detail_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("constraint evidence cannot be represented as finite JSON number")
    return number


def _extract_unique_calculation(
    calculations: list[Any], *, kind: str
) -> Mapping[str, Any]:
    matches = [
        item
        for item in calculations
        if isinstance(item, Mapping) and item.get("kind") == kind
    ]
    if not matches:
        raise ValueError(f"result.calculations must contain one {kind} calculation")
    if len(matches) != 1:
        raise ValueError(f"result.calculations must contain exactly one {kind} calculation")
    return matches[0]


def _compare(observed: Decimal, operator: str, required: Decimal) -> bool:
    if operator == "<":
        return observed < required
    if operator == "<=":
        return observed <= required
    if operator == "==":
        return observed == required
    if operator == "!=":
        return observed != required
    if operator == ">=":
        return observed >= required
    if operator == ">":
        return observed > required
    raise ValueError(f"unsupported constraint operator {operator!r}")


def _failure(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": CONSTRAINT_FRAMEWORK_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "result.calculations.constraints",
            "reason": reason,
            "details": {
                "validator_contract_version": CONSTRAINT_FRAMEWORK_CONTRACT_VERSION,
                "artifact_type": artifact_type,
            },
        }
    )


class DeterministicConstraintFrameworkValidator:
    """V7.9 generic deterministic numeric-constraint framework.

    Constraint thresholds are declared in the proposal/requirements data; this
    framework never invents domain policy. Each constraint references a scalar
    field from a supported V7.4-V7.8 calculation. Before evaluating a
    constraint, V7.9 executes that source validator on the same artifact and
    requires PASS, so the observed value is bound to deterministic engineering
    validation rather than an unverified duplicate scalar.

    V7.9 returns C1.7 ValidationResult only. It does not define the V7.10 issue
    schema, persist validation output, alter lifecycle, call a provider, or
    create any simulation verdict.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
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
            raise ConstraintFrameworkValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ConstraintFrameworkValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if CONSTRAINT_FRAMEWORK_VALIDATOR_ID not in task.validators:
            raise ConstraintFrameworkValidationConflictError(
                "producer task did not request the constraints validator"
            )

        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason="constraints validator currently requires ai.agent_result.v1",
                    ),
                )
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise ConstraintFrameworkValidationConflictError(
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

            calculations = payload.result.to_dict()["calculations"]
            constraint_set = _extract_unique_calculation(
                calculations, kind=CONSTRAINT_CALCULATION_KIND
            )
            if frozenset(constraint_set.keys()) != _CONSTRAINT_SET_KEYS:
                raise ValueError(
                    "constraints calculation must contain exactly kind,constraints"
                )
            raw_constraints = constraint_set["constraints"]
            if not isinstance(raw_constraints, list):
                raise ValueError("constraints.constraints must be an array")
            if not raw_constraints:
                raise ValueError("constraints.constraints must not be empty")
            if len(raw_constraints) > MAX_CONSTRAINTS:
                raise ValueError("constraints.constraints exceeds MAX_CONSTRAINTS")

            parsed_constraints: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for index, item in enumerate(raw_constraints):
                if not isinstance(item, Mapping) or frozenset(item.keys()) != _CONSTRAINT_KEYS:
                    raise ValueError(
                        f"constraints.constraints[{index}] must contain exactly "
                        "constraint_id,field,source_kind,source_field,operator,required_value,unit"
                    )
                cid = _constraint_id(
                    item["constraint_id"],
                    field=f"constraints.constraints[{index}].constraint_id",
                )
                if cid in seen_ids:
                    raise ValueError(f"duplicate constraint_id {cid!r}")
                seen_ids.add(cid)
                field_name = _clean_text(
                    item["field"],
                    field=f"constraints.constraints[{index}].field",
                    maximum=MAX_CONSTRAINT_FIELD_CHARS,
                )
                source_kind = _clean_text(
                    item["source_kind"],
                    field=f"constraints.constraints[{index}].source_kind",
                    maximum=MAX_CONSTRAINT_FIELD_CHARS,
                )
                source_field = _clean_text(
                    item["source_field"],
                    field=f"constraints.constraints[{index}].source_field",
                    maximum=MAX_CONSTRAINT_FIELD_CHARS,
                )
                operator = item["operator"]
                if operator not in CONSTRAINT_OPERATORS:
                    raise ValueError(
                        f"constraints.constraints[{index}].operator must be one of "
                        + ",".join(CONSTRAINT_OPERATORS)
                    )
                spec = _SOURCE_SPECS.get(source_kind)
                if spec is None:
                    raise ValueError(
                        f"unsupported constraint source_kind {source_kind!r}"
                    )
                expected_unit = spec.units.get(source_field)
                if expected_unit is None:
                    raise ValueError(
                        f"unsupported constraint source field {source_kind}.{source_field}"
                    )
                unit = item["unit"]
                if unit != expected_unit:
                    raise ValueError(
                        f"constraint unit for {source_kind}.{source_field} must be exactly {expected_unit}"
                    )
                required = _decimal_number(
                    item["required_value"],
                    field=f"constraints.constraints[{index}].required_value",
                )
                parsed_constraints.append(
                    {
                        "constraint_id": cid,
                        "field": field_name,
                        "source_kind": source_kind,
                        "source_field": source_field,
                        "operator": operator,
                        "required": required,
                        "unit": unit,
                    }
                )

            source_results: dict[str, ValidationResult] = {}
            source_calculations: dict[str, Mapping[str, Any]] = {}
            checks: list[ValidationCheck] = []

            for item in parsed_constraints:
                source_kind = item["source_kind"]
                spec = _SOURCE_SPECS[source_kind]
                if spec.validator_id not in task.validators:
                    raise ConstraintFrameworkValidationConflictError(
                        f"constraint source validator {spec.validator_id} was not requested by producer task"
                    )
                if source_kind not in source_results:
                    validator = spec.validator_type(self._project_store)
                    source_results[source_kind] = validator.validate(
                        record.project_id, record.artifact_id, record.version
                    )
                    source_calculations[source_kind] = _extract_unique_calculation(
                        calculations, kind=source_kind
                    )

                source_result = source_results[source_kind]
                source_calculation = source_calculations[source_kind]
                check_id = f"constraint.{item['constraint_id']}"
                if source_result.overall is not ValidationStatus.PASS:
                    checks.append(
                        ValidationCheck.from_dict(
                            {
                                "check_id": check_id,
                                "status": ValidationStatus.FAIL.value,
                                "field": item["field"],
                                "reason": f"source validator {spec.validator_id} did not PASS",
                                "details": {
                                    "validator_contract_version": CONSTRAINT_FRAMEWORK_CONTRACT_VERSION,
                                    "artifact_type": record.type,
                                    "constraint_id": item["constraint_id"],
                                    "source_kind": source_kind,
                                    "source_field": item["source_field"],
                                    "source_validator": spec.validator_id,
                                    "source_validation_status": source_result.overall.value,
                                    "operator": item["operator"],
                                    "unit": item["unit"],
                                    "required_value": _detail_number(item["required"]),
                                    "satisfied": False,
                                },
                            }
                        )
                    )
                    continue

                if item["source_field"] not in source_calculation:
                    raise ValueError(
                        f"source calculation {source_kind} does not contain {item['source_field']}"
                    )
                observed = _decimal_number(
                    source_calculation[item["source_field"]],
                    field=f"{source_kind}.{item['source_field']}",
                )
                satisfied = _compare(observed, item["operator"], item["required"])
                checks.append(
                    ValidationCheck.from_dict(
                        {
                            "check_id": check_id,
                            "status": (
                                ValidationStatus.PASS.value
                                if satisfied
                                else ValidationStatus.FAIL.value
                            ),
                            "field": item["field"],
                            "reason": None
                            if satisfied
                            else (
                                f"constraint {item['constraint_id']} failed: observed_value "
                                f"{item['operator']} required_value is false"
                            ),
                            "details": {
                                "validator_contract_version": CONSTRAINT_FRAMEWORK_CONTRACT_VERSION,
                                "artifact_type": record.type,
                                "constraint_id": item["constraint_id"],
                                "source_kind": source_kind,
                                "source_field": item["source_field"],
                                "source_validator": spec.validator_id,
                                "source_validation_status": source_result.overall.value,
                                "operator": item["operator"],
                                "unit": item["unit"],
                                "observed_value": _detail_number(observed),
                                "required_value": _detail_number(item["required"]),
                                "satisfied": satisfied,
                            },
                        }
                    )
                )

            return ValidationResult.from_checks(tuple(checks))

        except ConstraintFrameworkValidationConflictError:
            raise
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason=f"constraint framework validation failed: {exc}",
                    ),
                )
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise ConstraintFrameworkValidationConflictError(
                "constraint framework validator failed unexpectedly"
            ) from exc
