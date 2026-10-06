from __future__ import annotations

import json
import math
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


FINITE_NUMBER_VALIDATOR_CONTRACT_VERSION = "1.0"
# This identifier is already present in the frozen Phase-5 task contract/tests.
FINITE_NUMBER_VALIDATOR_ID = "finite_values"
MAX_FINITE_NUMBER_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES


class FiniteNumberValidationError(RuntimeError):
    """Base error for V7.2 deterministic finite-number validation."""


class FiniteNumberValidationConflictError(FiniteNumberValidationError):
    """Raised when validation cannot safely bind to persisted project state."""


def _strict_json_value(raw: bytes) -> Any:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_FINITE_NUMBER_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_FINITE_NUMBER_VALIDATION_BYTES")

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


def _scan_finite_numbers(value: Any, *, path: str = "$") -> int:
    """Return numeric-leaf count or fail on the first non-finite value.

    JSON booleans are deliberately not treated as numbers even though bool is a
    subclass of int in Python. Integers must also be representable as a finite
    binary64/Python float because later engineering calculators operate on
    ordinary finite floating-point values.
    """

    if value is None or isinstance(value, (bool, str)):
        return 0

    if isinstance(value, int):
        try:
            converted = float(value)
        except OverflowError as exc:
            raise ValueError(
                f"{path} integer exceeds finite floating-point range"
            ) from exc
        if not math.isfinite(converted):
            raise ValueError(f"{path} contains a non-finite number")
        return 1

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return 1

    if isinstance(value, list):
        total = 0
        for index, item in enumerate(value):
            total += _scan_finite_numbers(item, path=f"{path}[{index}]")
        return total

    if isinstance(value, Mapping):
        total = 0
        for key, item in value.items():
            total += _scan_finite_numbers(item, path=f"{path}.{key}")
        return total

    # json.loads should make this unreachable. Keep a fail-closed guard around
    # future decoder changes instead of silently accepting an unsupported type.
    raise ValueError(
        f"{path} contains unsupported decoded JSON type {type(value).__name__}"
    )


def _pass_check(*, artifact_type: str, numeric_count: int) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": FINITE_NUMBER_VALIDATOR_ID,
            "status": ValidationStatus.PASS.value,
            "field": "artifact.content",
            "reason": None,
            "details": {
                "validator_contract_version": FINITE_NUMBER_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "numeric_values_checked": numeric_count,
            },
        }
    )


def _fail_check(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": FINITE_NUMBER_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "artifact.content",
            "reason": reason,
            "details": {
                "validator_contract_version": FINITE_NUMBER_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
            },
        }
    )


class DeterministicFiniteNumberValidator:
    """V7.2 bounded deterministic finite-number validator.

    This validator is intentionally artifact-type agnostic. It first relies on
    Phase-4 content hashing for blob integrity, then scans the decoded strict
    JSON tree and rejects non-finite/overflowing numeric values. It returns C1.7
    ValidationResult only; it does not persist validation evidence or alter
    artifact/task lifecycle.
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
            raise FiniteNumberValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise FiniteNumberValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if FINITE_NUMBER_VALIDATOR_ID not in task.validators:
            raise FiniteNumberValidationConflictError(
                "producer task did not request the finite_values validator"
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise FiniteNumberValidationConflictError(
                "artifact content could not be read safely"
            ) from exc

        try:
            decoded = _strict_json_value(raw)
            numeric_count = _scan_finite_numbers(decoded)
        except (ValueError, TypeError) as exc:
            return ValidationResult.from_checks(
                (
                    _fail_check(
                        artifact_type=record.type,
                        reason=f"finite-number validation failed: {exc}",
                    ),
                )
            )
        except Exception as exc:  # fail closed around unexpected decoder bugs
            raise FiniteNumberValidationConflictError(
                "finite-number validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks(
            (_pass_check(artifact_type=record.type, numeric_count=numeric_count),)
        )
