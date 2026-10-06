from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


VALIDATION_RESULT_CONTRACT_VERSION = "1.0"


class ValidationResultError(ValueError):
    """Raised when deterministic validation output violates C1.7."""


class ValidationStatus(str, Enum):
    """Only deterministic PASS/FAIL outcomes are legal in C1.7."""

    PASS = "PASS"
    FAIL = "FAIL"


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


_VALIDATION_RESULT_KEYS = frozenset({"overall", "checks"})
_VALIDATION_CHECK_KEYS = frozenset(
    {
        "check_id",
        "status",
        "field",
        "reason",
        "details",
    }
)


def _require_clean_text(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValidationResultError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ValidationResultError(f"{field} must not be empty")
    if value != value.strip():
        raise ValidationResultError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


def _parse_status(raw: Any, *, field: str) -> ValidationStatus:
    if isinstance(raw, ValidationStatus):
        return raw
    if not isinstance(raw, str):
        raise ValidationResultError(
            f"{field} must be a string or ValidationStatus"
        )
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise ValidationResultError(f"{field} must not contain whitespace")
    try:
        return ValidationStatus(raw)
    except ValueError as exc:
        raise ValidationResultError(
            f"{field} must be exactly PASS or FAIL"
        ) from exc


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationResultError(
                f"{path} contains a non-finite number"
            )
        return value

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )

    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationResultError(
                    f"{path} contains a non-string object key"
                )
            if not key:
                raise ValidationResultError(
                    f"{path} contains an empty object key"
                )
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)

    raise ValidationResultError(
        f"{path} contains unsupported JSON value type "
        f"{type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class ValidationCheck:
    """One deterministic engineering check.

    `details` carries strict-JSON evidence such as observed/required values and
    units. It is data only; it cannot alter the outcome or invoke tools.
    """

    check_id: str
    status: ValidationStatus
    field: str | None
    reason: str | None
    details: Mapping[str, JSONValue]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidationCheck":
        if not isinstance(raw, Mapping):
            raise ValidationResultError(
                "validation check payload must be an object"
            )

        keys = frozenset(raw.keys())
        if keys != _VALIDATION_CHECK_KEYS:
            missing = sorted(_VALIDATION_CHECK_KEYS - keys)
            extra = sorted(keys - _VALIDATION_CHECK_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise ValidationResultError(
                "validation check payload must contain exactly the C1.7 fields"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        check_id = _require_clean_text(
            raw["check_id"],
            field="check_id",
        )
        status = _parse_status(raw["status"], field="status")

        field_value = raw["field"]
        if field_value is not None:
            field_value = _require_clean_text(
                field_value,
                field="field",
            )

        reason_value = raw["reason"]
        if reason_value is not None:
            reason_value = _require_clean_text(
                reason_value,
                field="reason",
            )

        if status is ValidationStatus.FAIL and reason_value is None:
            raise ValidationResultError(
                "FAIL validation check must include a non-empty reason"
            )

        raw_details = raw["details"]
        if not isinstance(raw_details, Mapping):
            raise ValidationResultError("details must be an object")
        frozen_details = _freeze_json(raw_details, path="details")
        assert isinstance(frozen_details, Mapping)

        return cls(
            check_id=check_id,
            status=status,
            field=field_value,
            reason=reason_value,
            details=frozen_details,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "field": self.field,
            "reason": self.reason,
            "details": _thaw_json(self.details),
        }


@dataclass(frozen=True)
class ValidationResult:
    """Structured deterministic engineering-validation result.

    Overall is constrained by child checks:
      - PASS iff every check is PASS
      - FAIL iff at least one check is FAIL

    C1.7 does not execute validators. Phase 7 will produce this contract.
    """

    overall: ValidationStatus
    checks: tuple[ValidationCheck, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidationResult":
        if not isinstance(raw, Mapping):
            raise ValidationResultError(
                "validation result payload must be an object"
            )

        keys = frozenset(raw.keys())
        if keys != _VALIDATION_RESULT_KEYS:
            missing = sorted(_VALIDATION_RESULT_KEYS - keys)
            extra = sorted(keys - _VALIDATION_RESULT_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise ValidationResultError(
                "validation result payload must contain exactly overall and checks"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        overall = _parse_status(raw["overall"], field="overall")

        raw_checks = raw["checks"]
        if not isinstance(raw_checks, (list, tuple)):
            raise ValidationResultError("checks must be an array")
        if not raw_checks:
            raise ValidationResultError(
                "validation result must contain at least one check"
            )

        checks = tuple(
            ValidationCheck.from_dict(item)
            for item in raw_checks
        )

        seen: set[str] = set()
        for check in checks:
            if check.check_id in seen:
                raise ValidationResultError(
                    f"duplicate validation check_id: {check.check_id}"
                )
            seen.add(check.check_id)

        derived = (
            ValidationStatus.FAIL
            if any(check.status is ValidationStatus.FAIL for check in checks)
            else ValidationStatus.PASS
        )
        if overall is not derived:
            raise ValidationResultError(
                "overall is inconsistent with validation checks: "
                f"declared={overall.value} derived={derived.value}"
            )

        return cls(overall=overall, checks=checks)

    @classmethod
    def from_checks(
        cls,
        checks: list[ValidationCheck] | tuple[ValidationCheck, ...],
    ) -> "ValidationResult":
        if not checks:
            raise ValidationResultError(
                "validation result must contain at least one check"
            )
        payload_checks = tuple(checks)
        seen: set[str] = set()
        for check in payload_checks:
            if not isinstance(check, ValidationCheck):
                raise ValidationResultError(
                    "from_checks requires ValidationCheck instances"
                )
            if check.check_id in seen:
                raise ValidationResultError(
                    f"duplicate validation check_id: {check.check_id}"
                )
            seen.add(check.check_id)

        overall = (
            ValidationStatus.FAIL
            if any(
                check.status is ValidationStatus.FAIL
                for check in payload_checks
            )
            else ValidationStatus.PASS
        )
        return cls(overall=overall, checks=payload_checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall.value,
            "checks": [
                check.to_dict()
                for check in self.checks
            ],
        }


def parse_validation_result(
    raw: Mapping[str, Any],
) -> ValidationResult:
    """Convenience parser for deterministic validator output."""

    return ValidationResult.from_dict(raw)
