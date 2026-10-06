from __future__ import annotations

import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from ai.agents.draft_artifact import MAX_RESULT_DRAFT_ARTIFACT_BYTES
from ai.artifacts.hashing import ArtifactContentStore, ArtifactHashError
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.ids import CanonicalId
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.store import ProjectStore
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


UNIT_VALIDATOR_CONTRACT_VERSION = "1.0"
UNIT_VALIDATOR_ID = "units"
MAX_UNIT_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES
MAX_UNIT_SCAN_NODES = 100_000
MAX_UNIT_SCAN_DEPTH = 128
MAX_UNIT_TEXT_CHARS = 16

# V7.3 freezes only the canonical spellings needed by the deterministic
# engineering calculator roadmap. It deliberately performs no conversion.
# Later calculators own dimensional requirements for their specific fields.
_CANONICAL_UNIT_DIMENSIONS = {
    "1": "dimensionless",
    "%": "ratio",
    "A": "current",
    "Ah": "charge_capacity",
    "N": "force",
    "V": "voltage",
    "W": "power",
    "Wh": "energy",
    "deg": "angle",
    "g": "mass",
    "h": "time",
    "kg": "mass",
    "km": "length",
    "m": "length",
    "m/s": "speed",
    "m/s^2": "acceleration",
    "min": "time",
    "rad": "angle",
    "s": "time",
}
CANONICAL_ENGINEERING_UNITS = MappingProxyType(_CANONICAL_UNIT_DIMENSIONS)

# Existing project and frozen-baseline field naming already encode units in
# suffixes such as mass_kg, capacity_wh, reserve_percent and gravity_m_s2.
# Longest suffixes must be checked first.
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_m_s2", "m/s^2"),
    ("_m_s", "m/s"),
    ("_percent", "%"),
    ("_pct", "%"),
    ("_wh", "Wh"),
    ("_ah", "Ah"),
    ("_deg", "deg"),
    ("_rad", "rad"),
    ("_min", "min"),
    ("_km", "km"),
    ("_kg", "kg"),
    ("_m", "m"),
    ("_s", "s"),
    ("_h", "h"),
    ("_g", "g"),
    ("_n", "N"),
    ("_v", "V"),
    ("_a", "A"),
    ("_w", "W"),
)


class UnitValidationError(RuntimeError):
    """Base error for V7.3 deterministic unit validation."""


class UnitValidationConflictError(UnitValidationError):
    """Raised when unit validation cannot safely bind to persisted state."""


def _strict_json_value(raw: bytes) -> Any:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_UNIT_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_UNIT_VALIDATION_BYTES")

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
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("artifact content is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("artifact content is invalid JSON") from exc


def _unit_from_field_name(name: str) -> str | None:
    if not isinstance(name, str):
        return None
    lower = name.lower()
    for suffix, unit in _UNIT_SUFFIXES:
        if lower.endswith(suffix):
            return unit
    return None


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        try:
            value = float(value)
        except OverflowError:
            return False
    return math.isfinite(value)


def _validate_unit_text(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{path} must be clean non-empty text")
    if len(value) > MAX_UNIT_TEXT_CHARS:
        raise ValueError(f"{path} exceeds MAX_UNIT_TEXT_CHARS")
    if value not in CANONICAL_ENGINEERING_UNITS:
        raise ValueError(f"{path} uses unsupported or non-canonical unit {value!r}")
    return value


def _scan_units(value: Any) -> tuple[int, int]:
    """Validate declared units and canonical unit suffixes iteratively.

    Returns `(unit_annotations_checked, suffix_values_checked)`.

    This is intentionally not a dimensional-completeness validator. Numbers
    without an explicit `unit` declaration or a recognized canonical suffix are
    ignored until a later domain calculator defines their required dimension.
    """

    stack: list[tuple[Any, str, int, str | None]] = [(value, "$", 0, None)]
    nodes = 0
    unit_annotations = 0
    suffix_values = 0

    while stack:
        current, path, depth, parent_key = stack.pop()
        nodes += 1
        if nodes > MAX_UNIT_SCAN_NODES:
            raise ValueError("unit scan exceeds MAX_UNIT_SCAN_NODES")
        if depth > MAX_UNIT_SCAN_DEPTH:
            raise ValueError("unit scan exceeds MAX_UNIT_SCAN_DEPTH")

        if isinstance(current, Mapping):
            declared_unit: str | None = None
            if "unit" in current:
                declared_unit = _validate_unit_text(
                    current["unit"], path=f"{path}.unit"
                )
                unit_annotations += 1

                expected_from_parent = (
                    _unit_from_field_name(parent_key)
                    if parent_key is not None
                    else None
                )
                if (
                    expected_from_parent is not None
                    and declared_unit != expected_from_parent
                ):
                    raise ValueError(
                        f"{path}.unit={declared_unit!r} conflicts with "
                        f"field suffix unit {expected_from_parent!r}"
                    )

                # When an object explicitly uses the conventional quantity
                # member `value`, it must actually be a finite numeric value.
                # Objects may instead use observed/limit/etc. with one shared
                # unit annotation, so `value` itself is not mandatory.
                if "value" in current and not _is_finite_number(current["value"]):
                    raise ValueError(
                        f"{path}.value must be a finite number when unit is declared"
                    )

            # Reverse lexical order makes traversal stable when popped.
            for key in sorted(current.keys(), reverse=True):
                item = current[key]
                item_path = f"{path}.{key}"

                expected = _unit_from_field_name(key)
                if expected is not None and _is_finite_number(item):
                    suffix_values += 1

                if key == "unit":
                    continue
                stack.append((item, item_path, depth + 1, key))
            continue

        if isinstance(current, list):
            for index in range(len(current) - 1, -1, -1):
                stack.append(
                    (current[index], f"{path}[{index}]", depth + 1, parent_key)
                )
            continue

        # Scalars require no action unless their containing field encoded a
        # canonical unit suffix. That count is handled by the parent mapping.

    return unit_annotations, suffix_values


def _pass_check(
    *,
    artifact_type: str,
    unit_annotations: int,
    suffix_values: int,
) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": UNIT_VALIDATOR_ID,
            "status": ValidationStatus.PASS.value,
            "field": "artifact.content",
            "reason": None,
            "details": {
                "validator_contract_version": UNIT_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "canonical_unit_registry_size": len(CANONICAL_ENGINEERING_UNITS),
                "unit_annotations_checked": unit_annotations,
                "unit_suffix_values_checked": suffix_values,
            },
        }
    )


def _fail_check(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": UNIT_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "artifact.content",
            "reason": reason,
            "details": {
                "validator_contract_version": UNIT_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
            },
        }
    )


class DeterministicUnitValidator:
    """V7.3 bounded deterministic unit-declaration validator.

    Phase-4 content hashing remains authoritative for blob integrity. V7.3 then
    validates canonical engineering unit declarations and established unit
    suffixes. It returns only C1.7 ValidationResult and deliberately does not
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
            raise UnitValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise UnitValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if UNIT_VALIDATOR_ID not in task.validators:
            raise UnitValidationConflictError(
                "producer task did not request the units validator"
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise UnitValidationConflictError(
                "artifact content could not be read safely"
            ) from exc

        try:
            decoded = _strict_json_value(raw)
            unit_annotations, suffix_values = _scan_units(decoded)
        except (ValueError, TypeError) as exc:
            return ValidationResult.from_checks(
                (
                    _fail_check(
                        artifact_type=record.type,
                        reason=f"unit validation failed: {exc}",
                    ),
                )
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise UnitValidationConflictError(
                "unit validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks(
            (
                _pass_check(
                    artifact_type=record.type,
                    unit_annotations=unit_annotations,
                    suffix_values=suffix_values,
                ),
            )
        )
