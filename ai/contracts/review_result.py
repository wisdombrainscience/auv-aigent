from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind


REVIEW_RESULT_CONTRACT_VERSION = "1.0"
REVIEW_ISSUE_CONTRACT_VERSION = "1.0"


class ReviewResultError(ValueError):
    """Raised when structured reviewer output violates the C1.6 contract."""


_REVIEW_RESULT_KEYS = frozenset({"issues"})
_REVIEW_ISSUE_KEYS = frozenset(
    {
        "issue_id",
        "fingerprint",
        "severity",
        "category",
        "field",
        "reason",
        "required_action",
    }
)


def _require_clean_text(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ReviewResultError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ReviewResultError(f"{field} must not be empty")
    if value != value.strip():
        raise ReviewResultError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


def _parse_issue_id(raw: Any) -> CanonicalId:
    if isinstance(raw, CanonicalId):
        canonical = raw
    else:
        try:
            canonical = CanonicalId.parse(raw)
        except (CanonicalIdError, TypeError) as exc:
            raise ReviewResultError("issue_id must be a canonical ISSUE ID") from exc

    if canonical.kind is not IdKind.ISSUE:
        raise ReviewResultError("issue_id must use the ISSUE ID namespace")
    return canonical


@dataclass(frozen=True)
class ReviewIssue:
    """One immutable advisory issue raised by the AI reviewer.

    The frozen architecture requires issues to carry stable root-cause identity
    so repeated identical findings can later be deduplicated. C1.6 therefore
    requires a non-empty fingerprint, but does not prescribe its hashing
    algorithm; duplicate suppression across reviews belongs to Phase 9.
    """

    issue_id: CanonicalId
    fingerprint: str
    severity: str
    category: str
    field: str | None
    reason: str
    required_action: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReviewIssue":
        if not isinstance(raw, Mapping):
            raise ReviewResultError("review issue payload must be an object")

        keys = frozenset(raw.keys())
        if keys != _REVIEW_ISSUE_KEYS:
            missing = sorted(_REVIEW_ISSUE_KEYS - keys)
            extra = sorted(keys - _REVIEW_ISSUE_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise ReviewResultError(
                "review issue payload must contain exactly the C1.6 fields"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        field_value = raw["field"]
        if field_value is not None:
            field_value = _require_clean_text(
                field_value,
                field="field",
            )

        return cls(
            issue_id=_parse_issue_id(raw["issue_id"]),
            fingerprint=_require_clean_text(
                raw["fingerprint"],
                field="fingerprint",
            ),
            severity=_require_clean_text(
                raw["severity"],
                field="severity",
            ),
            category=_require_clean_text(
                raw["category"],
                field="category",
            ),
            field=field_value,
            reason=_require_clean_text(
                raw["reason"],
                field="reason",
            ),
            required_action=_require_clean_text(
                raw["required_action"],
                field="required_action",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id.value,
            "fingerprint": self.fingerprint,
            "severity": self.severity,
            "category": self.category,
            "field": self.field,
            "reason": self.reason,
            "required_action": self.required_action,
        }


def parse_review_issue(raw: Mapping[str, Any]) -> ReviewIssue:
    """Parse one frozen Phase-9 advisory review issue.

    R9.1 promotes the C1.6 embedded issue object as the standalone review/feedback
    issue schema without changing its wire fields or authority boundary. Allocation,
    persistence, owner routing, rework generation, and cross-review suppression remain
    later Phase-9 responsibilities.
    """

    return ReviewIssue.from_dict(raw)


@dataclass(frozen=True)
class ReviewResult:
    """Minimum structured reviewer result supported by the frozen baseline.

    A review result is advisory issue output only. It intentionally has no
    PASS/FAIL, APPROVED, artifact replacement, patch, or mutation field.
    Deterministic validators and the independent UAV verifier retain their
    separate authorities.
    """

    issues: tuple[ReviewIssue, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReviewResult":
        if not isinstance(raw, Mapping):
            raise ReviewResultError("review result payload must be an object")

        keys = frozenset(raw.keys())
        if keys != _REVIEW_RESULT_KEYS:
            missing = sorted(_REVIEW_RESULT_KEYS - keys)
            extra = sorted(keys - _REVIEW_RESULT_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise ReviewResultError(
                "review result payload must contain exactly issues"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        raw_issues = raw["issues"]
        if not isinstance(raw_issues, (list, tuple)):
            raise ReviewResultError("issues must be an array")

        issues = tuple(
            ReviewIssue.from_dict(item)
            for item in raw_issues
        )

        seen_ids: set[str] = set()
        seen_fingerprints: set[str] = set()
        for issue in issues:
            if issue.issue_id.value in seen_ids:
                raise ReviewResultError(
                    f"duplicate issue_id in one review result: "
                    f"{issue.issue_id.value}"
                )
            if issue.fingerprint in seen_fingerprints:
                raise ReviewResultError(
                    "duplicate issue fingerprint in one review result: "
                    f"{issue.fingerprint}"
                )
            seen_ids.add(issue.issue_id.value)
            seen_fingerprints.add(issue.fingerprint)

        return cls(issues=issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [
                issue.to_dict()
                for issue in self.issues
            ]
        }


def parse_review_result(raw: Mapping[str, Any]) -> ReviewResult:
    """Convenience parser for reviewer/provider JSON after decoding."""

    return ReviewResult.from_dict(raw)
