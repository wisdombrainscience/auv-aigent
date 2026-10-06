from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


VALIDATION_ISSUE_CONTRACT_VERSION = "1.0"
VALIDATION_ISSUE_FINGERPRINT_ALGORITHM = "sha256"
MAX_VALIDATION_ISSUES = 256


class ValidationIssueError(ValueError):
    """Raised when a deterministic V7.10 validation issue violates its schema."""


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]

_VALIDATION_ISSUE_KEYS = frozenset(
    {
        "fingerprint",
        "check_id",
        "field",
        "reason",
        "details",
    }
)
_VALIDATION_ISSUE_SET_KEYS = frozenset({"issues"})
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require_clean_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationIssueError(f"{field} must be a string")
    if not value:
        raise ValidationIssueError(f"{field} must not be empty")
    if value != value.strip():
        raise ValidationIssueError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValidationIssueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValidationIssueError(
                    f"{path} contains a non-string or empty object key"
                )
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    raise ValidationIssueError(
        f"{path} contains unsupported JSON value type {type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def _canonical_root_cause_bytes(*, check_id: str, field: str | None, reason: str) -> bytes:
    payload = {
        "check_id": check_id,
        "field": field,
        "reason": reason,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def validation_issue_fingerprint(
    *,
    check_id: str,
    field: str | None,
    reason: str,
) -> str:
    """Return stable root-cause identity for one failed C1.7 check.

    Evidence/details are intentionally excluded. Repeated validation of the same
    root cause may produce different observed numeric evidence; that must not
    create an unbounded stream of distinct issue identities.
    """

    canonical_check_id = _require_clean_text(check_id, field="check_id")
    canonical_reason = _require_clean_text(reason, field="reason")
    canonical_field: str | None = field
    if canonical_field is not None:
        canonical_field = _require_clean_text(canonical_field, field="field")

    digest = hashlib.sha256(
        _canonical_root_cause_bytes(
            check_id=canonical_check_id,
            field=canonical_field,
            reason=canonical_reason,
        )
    ).hexdigest()
    return f"{VALIDATION_ISSUE_FINGERPRINT_ALGORITHM}:{digest}"


@dataclass(frozen=True)
class ValidationIssue:
    """One immutable deterministic issue derived from a failed C1.7 check.

    V7.10 deliberately has no canonical ISSUE ID, severity, owner, routing,
    required-action, lifecycle mutation, or persistence field. Those belong to
    later review/rework orchestration. The stable fingerprint is the handoff key
    that later phases may use for allocation/deduplication.
    """

    fingerprint: str
    check_id: str
    field: str | None
    reason: str
    details: Mapping[str, JSONValue]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidationIssue":
        if not isinstance(raw, Mapping):
            raise ValidationIssueError("validation issue payload must be an object")

        keys = frozenset(raw.keys())
        if keys != _VALIDATION_ISSUE_KEYS:
            missing = sorted(_VALIDATION_ISSUE_KEYS - keys)
            extra = sorted(keys - _VALIDATION_ISSUE_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise ValidationIssueError(
                "validation issue payload must contain exactly the V7.10 fields"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        fingerprint = _require_clean_text(raw["fingerprint"], field="fingerprint")
        if _FINGERPRINT_RE.fullmatch(fingerprint) is None:
            raise ValidationIssueError(
                "fingerprint must be sha256: followed by exactly 64 lowercase hex digits"
            )

        check_id = _require_clean_text(raw["check_id"], field="check_id")
        field_value = raw["field"]
        if field_value is not None:
            field_value = _require_clean_text(field_value, field="field")
        reason = _require_clean_text(raw["reason"], field="reason")

        expected = validation_issue_fingerprint(
            check_id=check_id,
            field=field_value,
            reason=reason,
        )
        if fingerprint != expected:
            raise ValidationIssueError(
                "fingerprint does not match deterministic root-cause identity"
            )

        raw_details = raw["details"]
        if not isinstance(raw_details, Mapping):
            raise ValidationIssueError("details must be an object")
        frozen = _freeze_json(raw_details, path="details")
        assert isinstance(frozen, Mapping)

        return cls(
            fingerprint=fingerprint,
            check_id=check_id,
            field=field_value,
            reason=reason,
            details=frozen,
        )

    @classmethod
    def from_failed_check(cls, check: ValidationCheck) -> "ValidationIssue":
        if not isinstance(check, ValidationCheck):
            raise ValidationIssueError("check must be ValidationCheck")
        if check.status is not ValidationStatus.FAIL:
            raise ValidationIssueError("validation issues can be created only from FAIL checks")
        if check.reason is None:
            raise ValidationIssueError("FAIL check must carry a reason")

        payload = check.to_dict()
        fingerprint = validation_issue_fingerprint(
            check_id=check.check_id,
            field=check.field,
            reason=check.reason,
        )
        return cls.from_dict(
            {
                "fingerprint": fingerprint,
                "check_id": check.check_id,
                "field": check.field,
                "reason": check.reason,
                "details": payload["details"],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "check_id": self.check_id,
            "field": self.field,
            "reason": self.reason,
            "details": _thaw_json(self.details),
        }


@dataclass(frozen=True)
class ValidationIssueSet:
    """Ordered immutable V7.10 issue envelope for one C1.7 result."""

    issues: tuple[ValidationIssue, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidationIssueSet":
        if not isinstance(raw, Mapping):
            raise ValidationIssueError("validation issue set payload must be an object")
        if frozenset(raw.keys()) != _VALIDATION_ISSUE_SET_KEYS:
            raise ValidationIssueError(
                "validation issue set payload must contain exactly issues"
            )

        raw_issues = raw["issues"]
        if not isinstance(raw_issues, (list, tuple)):
            raise ValidationIssueError("issues must be an array")
        if len(raw_issues) > MAX_VALIDATION_ISSUES:
            raise ValidationIssueError(
                f"issues exceeds MAX_VALIDATION_ISSUES={MAX_VALIDATION_ISSUES}"
            )

        issues = tuple(ValidationIssue.from_dict(item) for item in raw_issues)
        seen: set[str] = set()
        for issue in issues:
            if issue.fingerprint in seen:
                raise ValidationIssueError(
                    f"duplicate validation issue fingerprint: {issue.fingerprint}"
                )
            seen.add(issue.fingerprint)
        return cls(issues=issues)

    @classmethod
    def from_validation_result(cls, result: ValidationResult) -> "ValidationIssueSet":
        if not isinstance(result, ValidationResult):
            raise ValidationIssueError("result must be ValidationResult")

        failed = tuple(
            check
            for check in result.checks
            if check.status is ValidationStatus.FAIL
        )
        if len(failed) > MAX_VALIDATION_ISSUES:
            raise ValidationIssueError(
                f"failed checks exceeds MAX_VALIDATION_ISSUES={MAX_VALIDATION_ISSUES}"
            )

        return cls.from_dict(
            {
                "issues": [
                    ValidationIssue.from_failed_check(check).to_dict()
                    for check in failed
                ]
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {"issues": [issue.to_dict() for issue in self.issues]}


def normalize_validation_issues(result: ValidationResult) -> ValidationIssueSet:
    """Normalize only failed checks; PASS results deterministically yield no issues."""

    return ValidationIssueSet.from_validation_result(result)
