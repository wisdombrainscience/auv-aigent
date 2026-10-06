from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
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


MISSION_DISTANCE_VALIDATOR_CONTRACT_VERSION = "1.0"
MISSION_DISTANCE_VALIDATOR_ID = "mission_distance"
MISSION_DISTANCE_CALCULATION_KIND = "mission_distance"
MISSION_DISTANCE_FRAME = "LOCAL_NED"
MAX_MISSION_DISTANCE_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES
MAX_MISSION_DISTANCE_POINTS = 24
MISSION_DISTANCE_DECIMAL_PRECISION = 50
MISSION_DISTANCE_QUANTUM_M = Decimal("0.000001")

_MISSION_DISTANCE_KEYS = frozenset(
    {
        "kind",
        "frame",
        "points_m",
        "reported_distance_m",
    }
)


class MissionDistanceValidationError(RuntimeError):
    """Base error for V7.8 deterministic mission-distance validation."""


class MissionDistanceValidationConflictError(MissionDistanceValidationError):
    """Raised when V7.8 cannot bind safely to persisted project state."""


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_MISSION_DISTANCE_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_MISSION_DISTANCE_VALIDATION_BYTES")

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


def _decimal_coordinate(value: Any, *, field: str) -> Decimal:
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


def _decimal_nonnegative(value: Any, *, field: str) -> Decimal:
    number = _decimal_coordinate(value, field=field)
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _extract_calculation(payload: ResultDraftArtifactPayload) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for item in payload.result.to_dict()["calculations"]:
        if isinstance(item, Mapping) and item.get("kind") == MISSION_DISTANCE_CALCULATION_KIND:
            matches.append(item)
    if not matches:
        raise ValueError("result.calculations must contain one mission_distance calculation")
    if len(matches) != 1:
        raise ValueError("result.calculations must contain exactly one mission_distance calculation")
    return matches[0]


def _parse_points(raw: Any) -> tuple[tuple[Decimal, Decimal, Decimal], ...]:
    if not isinstance(raw, list):
        raise ValueError("mission_distance.points_m must be an array")
    if len(raw) < 2:
        raise ValueError("mission_distance.points_m must contain at least two points")
    if len(raw) > MAX_MISSION_DISTANCE_POINTS:
        raise ValueError("mission_distance.points_m exceeds MAX_MISSION_DISTANCE_POINTS")

    points: list[tuple[Decimal, Decimal, Decimal]] = []
    for index, point in enumerate(raw):
        if not isinstance(point, list) or len(point) != 3:
            raise ValueError(
                f"mission_distance.points_m[{index}] must be [north_m,east_m,down_m]"
            )
        points.append(
            (
                _decimal_coordinate(point[0], field=f"mission_distance.points_m[{index}][0]"),
                _decimal_coordinate(point[1], field=f"mission_distance.points_m[{index}][1]"),
                _decimal_coordinate(point[2], field=f"mission_distance.points_m[{index}][2]"),
            )
        )
    return tuple(points)


def _compute_distance(
    points: tuple[tuple[Decimal, Decimal, Decimal], ...],
) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MISSION_DISTANCE_DECIMAL_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        total = Decimal("0")
        for start, end in zip(points, points[1:]):
            dn = end[0] - start[0]
            de = end[1] - start[1]
            dd = end[2] - start[2]
            squared = dn * dn + de * de + dd * dd
            if not squared.is_finite() or squared < 0:
                raise ValueError("computed mission-distance segment squared norm is invalid")
            segment = squared.sqrt(context=ctx)
            if not segment.is_finite():
                raise ValueError("computed mission-distance segment is not finite")
            total += segment
        if not total.is_finite():
            raise ValueError("computed mission distance is not finite")
        try:
            return total.quantize(MISSION_DISTANCE_QUANTUM_M, rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError("computed mission distance cannot be quantized safely") from exc


def _evaluate(
    calculation: Mapping[str, Any],
) -> tuple[int, Decimal, Decimal, bool]:
    if frozenset(calculation.keys()) != _MISSION_DISTANCE_KEYS:
        raise ValueError(
            "mission_distance calculation must contain exactly "
            "kind,frame,points_m,reported_distance_m"
        )
    if calculation["kind"] != MISSION_DISTANCE_CALCULATION_KIND:
        raise ValueError("mission_distance kind mismatch")
    if calculation["frame"] != MISSION_DISTANCE_FRAME:
        raise ValueError("mission_distance.frame must be exactly LOCAL_NED")

    points = _parse_points(calculation["points_m"])
    computed = _compute_distance(points)
    reported = _decimal_nonnegative(
        calculation["reported_distance_m"],
        field="mission_distance.reported_distance_m",
    )
    reconciled = computed == reported
    return len(points), computed, reported, reconciled


def _detail_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("mission-distance evidence cannot be represented as finite JSON number")
    return number


def _check(
    *,
    artifact_type: str,
    point_count: int,
    computed: Decimal,
    reported: Decimal,
    reconciled: bool,
) -> ValidationCheck:
    status = ValidationStatus.PASS if reconciled else ValidationStatus.FAIL
    reason = None if reconciled else (
        "reported_distance_m does not equal deterministic LOCAL_NED 3D polyline distance"
    )
    return ValidationCheck.from_dict(
        {
            "check_id": MISSION_DISTANCE_VALIDATOR_ID,
            "status": status.value,
            "field": "result.calculations.mission_distance",
            "reason": reason,
            "details": {
                "validator_contract_version": MISSION_DISTANCE_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "frame": MISSION_DISTANCE_FRAME,
                "unit": "m",
                "formula": "sum(sqrt(dn^2+de^2+dd^2))",
                "point_count": point_count,
                "segment_count": point_count - 1,
                "decimal_precision": MISSION_DISTANCE_DECIMAL_PRECISION,
                "quantum_m": float(MISSION_DISTANCE_QUANTUM_M),
                "rounding": "ROUND_HALF_EVEN",
                "computed_distance_m": _detail_number(computed),
                "reported_distance_m": _detail_number(reported),
                "reconciled": reconciled,
            },
        }
    )


def _failure(*, artifact_type: str, reason: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": MISSION_DISTANCE_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": "result.calculations.mission_distance",
            "reason": reason,
            "details": {
                "validator_contract_version": MISSION_DISTANCE_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "frame": MISSION_DISTANCE_FRAME,
                "unit": "m",
                "formula": "sum(sqrt(dn^2+de^2+dd^2))",
                "decimal_precision": MISSION_DISTANCE_DECIMAL_PRECISION,
                "quantum_m": float(MISSION_DISTANCE_QUANTUM_M),
                "rounding": "ROUND_HALF_EVEN",
            },
        }
    )


class DeterministicMissionDistanceValidator:
    """V7.8 deterministic LOCAL_NED mission-distance calculator/validator.

    V7.8 validates one explicitly tagged ``mission_distance`` calculation in
    the current G6.8 ``ai.agent_result.v1`` artifact. The calculation supplies
    a bounded 3D LOCAL_NED polyline as ``[north,east,down]`` metre points.
    Segment Euclidean lengths are summed under a fixed Decimal context and the
    final total is quantized to one micrometre with ROUND_HALF_EVEN before
    reconciliation with the reported distance.

    This validator returns C1.7 ValidationResult only. It does not execute a
    mission, inspect telemetry, persist validation evidence, alter task/artifact
    lifecycle, or create the simulation verdict reserved to the independent UAV
    verifier.
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
            raise MissionDistanceValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise MissionDistanceValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if MISSION_DISTANCE_VALIDATOR_ID not in task.validators:
            raise MissionDistanceValidationConflictError(
                "producer task did not request the mission_distance validator"
            )

        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            return ValidationResult.from_checks(
                (
                    _failure(
                        artifact_type=record.type,
                        reason="mission_distance validator currently requires ai.agent_result.v1",
                    ),
                )
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise MissionDistanceValidationConflictError(
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
            point_count, computed, reported, reconciled = _evaluate(calculation)
            check = _check(
                artifact_type=record.type,
                point_count=point_count,
                computed=computed,
                reported=reported,
                reconciled=reconciled,
            )
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            check = _failure(
                artifact_type=record.type,
                reason=f"mission_distance validation failed: {exc}",
            )
        except Exception as exc:  # fail closed around unexpected validator bugs
            raise MissionDistanceValidationConflictError(
                "mission_distance validator failed unexpectedly"
            ) from exc

        return ValidationResult.from_checks((check,))
