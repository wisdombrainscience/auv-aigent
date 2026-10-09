from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.failures import FailureClass, FailureDescriptor
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.simulation_feedback.parser import ParsedVerifierResult


SIMULATION_ISSUE_TAXONOMY_CONTRACT_VERSION = "1.0"
SIMULATION_ISSUE_FINGERPRINT_ALGORITHM = "sha256"
MAX_SIMULATION_ISSUES = 7


class SimulationIssueTaxonomyError(ValueError):
    """Raised when an F12.2 simulation issue violates the frozen contract."""


class SimulationIssueType(str, Enum):
    """Deterministic feedback categories derived only from verifier checks."""

    MISSION_SEQUENCE = "MISSION_SEQUENCE"
    CAPABILITY_VERDICT = "CAPABILITY_VERDICT"
    EVIDENCE_INTEGRITY = "EVIDENCE_INTEGRITY"
    NAVIGATION = "NAVIGATION"
    MISSION_TERMINATION = "MISSION_TERMINATION"
    LANDING = "LANDING"
    RETURN_HOME = "RETURN_HOME"


@dataclass(frozen=True)
class _IssueSpec:
    issue_type: SimulationIssueType
    failure_class: FailureClass
    code: str
    message: str


_ISSUE_SPECS: Mapping[str, _IssueSpec] = MappingProxyType(
    {
        "step_sequence": _IssueSpec(
            SimulationIssueType.MISSION_SEQUENCE,
            FailureClass.VERIFICATION,
            "VERIFICATION_MISSION_SEQUENCE",
            "Mission execution sequence failed independent verification.",
        ),
        "child_verdicts": _IssueSpec(
            SimulationIssueType.CAPABILITY_VERDICT,
            FailureClass.VERIFICATION,
            "VERIFICATION_CHILD_VERDICT",
            "One or more child capability verdicts failed independent verification.",
        ),
        "child_evidence_integrity": _IssueSpec(
            SimulationIssueType.EVIDENCE_INTEGRITY,
            FailureClass.VERIFICATION,
            "VERIFICATION_CHILD_EVIDENCE_INTEGRITY",
            "Child capability evidence integrity failed independent verification.",
        ),
        "continuity": _IssueSpec(
            SimulationIssueType.NAVIGATION,
            FailureClass.SIMULATION,
            "SIMULATION_NAVIGATION_CONTINUITY",
            "Mission transition continuity failed simulation verification.",
        ),
        "terminal_land_step": _IssueSpec(
            SimulationIssueType.MISSION_TERMINATION,
            FailureClass.VERIFICATION,
            "VERIFICATION_TERMINAL_LAND_STEP",
            "Mission termination sequence did not end with the required land capability.",
        ),
        "terminal_landed_state": _IssueSpec(
            SimulationIssueType.LANDING,
            FailureClass.SIMULATION,
            "SIMULATION_TERMINAL_LANDED_STATE",
            "Simulation did not finish in the required landed state.",
        ),
        "terminal_home": _IssueSpec(
            SimulationIssueType.RETURN_HOME,
            FailureClass.SIMULATION,
            "SIMULATION_TERMINAL_HOME_POSITION",
            "Simulation terminal position failed the home-position requirement.",
        ),
    }
)

_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _fingerprint(*, check_id: str, issue_type: SimulationIssueType, code: str) -> str:
    # Root-cause identity intentionally excludes run ID, observed metrics, free-form
    # verifier reason, and failed step IDs. Later bounded retry/deduplication may use
    # this stable identity without treating repeated observations as new root causes.
    payload = {
        "check_id": check_id,
        "issue_type": issue_type.value,
        "code": code,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{SIMULATION_ISSUE_FINGERPRINT_ALGORITHM}:{hashlib.sha256(raw).hexdigest()}"


def _details(result: ParsedVerifierResult, *, check_id: str) -> dict[str, Any]:
    details: dict[str, Any] = {
        "check_id": check_id,
        "runtime_run_id": result.runtime_run_id,
        "mission_id": result.mission_id,
        "scenario_id": result.scenario_id,
        "failed_step_ids": list(result.failed_step_ids),
        "verifier_reason": result.reason,
    }
    if check_id == "continuity":
        details["max_transition_distance_m"] = result.max_transition_distance_m
    elif check_id == "terminal_home":
        details["terminal_home_distance_m"] = result.terminal_home_distance_m
    return details


@dataclass(frozen=True)
class SimulationIssue:
    """One normalized F12.2 issue derived from one failed verifier check.

    This is classification only. It allocates no canonical ISSUE ID, chooses no
    engineering owner, mutates no artifact lineage, creates no task, and grants
    no simulation retry permission.
    """

    fingerprint: str
    issue_type: SimulationIssueType
    check_id: str
    failure: FailureDescriptor
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.issue_type, SimulationIssueType):
            raise SimulationIssueTaxonomyError("issue_type must be SimulationIssueType")
        if not isinstance(self.check_id, str) or not self.check_id:
            raise SimulationIssueTaxonomyError("check_id must be clean non-empty text")
        if self.check_id not in _ISSUE_SPECS:
            raise SimulationIssueTaxonomyError(f"unknown verifier check: {self.check_id}")
        if _FINGERPRINT_RE.fullmatch(self.fingerprint) is None:
            raise SimulationIssueTaxonomyError(
                "fingerprint must be sha256: followed by 64 lowercase hex digits"
            )
        if not isinstance(self.failure, FailureDescriptor):
            raise SimulationIssueTaxonomyError("failure must be FailureDescriptor")
        spec = _ISSUE_SPECS[self.check_id]
        if self.issue_type is not spec.issue_type:
            raise SimulationIssueTaxonomyError("issue_type does not match verifier check")
        if self.failure.failure_class is not spec.failure_class:
            raise SimulationIssueTaxonomyError("failure class does not match taxonomy")
        if self.failure.code != spec.code:
            raise SimulationIssueTaxonomyError("failure code does not match taxonomy")
        expected = _fingerprint(
            check_id=self.check_id,
            issue_type=self.issue_type,
            code=self.failure.code,
        )
        if self.fingerprint != expected:
            raise SimulationIssueTaxonomyError(
                "fingerprint does not match deterministic root-cause identity"
            )
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationIssueTaxonomyError("provenance must remain VERIFIER_RESULT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "issue_type": self.issue_type.value,
            "check_id": self.check_id,
            "failure": self.failure.to_dict(),
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True)
class SimulationIssueSet:
    """Immutable F12.2 envelope preserving exact verifier/project lineage."""

    project_id: CanonicalId
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    runtime_run_id: str
    mission_id: str
    scenario_id: str
    verification_sha256: str
    issues: tuple[SimulationIssue, ...]
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise SimulationIssueTaxonomyError("project_id must be a canonical PROJECT ID")
        if isinstance(self.requirement_version, bool) or not isinstance(self.requirement_version, int) or self.requirement_version <= 0:
            raise SimulationIssueTaxonomyError("requirement_version must be a positive integer")
        if not isinstance(self.source_artifacts, tuple):
            raise SimulationIssueTaxonomyError("source_artifacts must be a tuple")
        if any(not isinstance(item, ArtifactInputVersion) for item in self.source_artifacts):
            raise SimulationIssueTaxonomyError("source_artifacts contains invalid lineage")
        for field, value in (
            ("runtime_run_id", self.runtime_run_id),
            ("mission_id", self.mission_id),
            ("scenario_id", self.scenario_id),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise SimulationIssueTaxonomyError(f"{field} must be clean non-empty text")
        if re.fullmatch(r"[0-9a-f]{64}", self.verification_sha256) is None:
            raise SimulationIssueTaxonomyError("verification_sha256 must be lowercase sha256")
        if not isinstance(self.issues, tuple):
            raise SimulationIssueTaxonomyError("issues must be a tuple")
        if len(self.issues) > MAX_SIMULATION_ISSUES:
            raise SimulationIssueTaxonomyError("issues exceeds bounded size")
        if any(not isinstance(item, SimulationIssue) for item in self.issues):
            raise SimulationIssueTaxonomyError("issues contains invalid item")
        fingerprints = tuple(item.fingerprint for item in self.issues)
        if len(set(fingerprints)) != len(fingerprints):
            raise SimulationIssueTaxonomyError("issues contains duplicate fingerprint")
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationIssueTaxonomyError("provenance must remain VERIFIER_RESULT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": SIMULATION_ISSUE_TAXONOMY_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "runtime_run_id": self.runtime_run_id,
            "mission_id": self.mission_id,
            "scenario_id": self.scenario_id,
            "verification_sha256": self.verification_sha256,
            "issues": [issue.to_dict() for issue in self.issues],
            "provenance": self.provenance.value,
        }


def classify_simulation_issues(result: ParsedVerifierResult) -> SimulationIssueSet:
    """Normalize verifier FAIL checks into deterministic simulation-feedback issues.

    PASS yields an empty issue set. No persistence, routing, invalidation, rework,
    retry, or human-gate transition occurs in F12.2.
    """

    if not isinstance(result, ParsedVerifierResult):
        raise TypeError("result must be ParsedVerifierResult")

    issues: list[SimulationIssue] = []
    for check_id in result.failed_checks:
        spec = _ISSUE_SPECS[check_id]
        failure = FailureDescriptor.from_dict(
            {
                "class": spec.failure_class.value,
                "code": spec.code,
                "message": spec.message,
                "details": _details(result, check_id=check_id),
            }
        )
        issues.append(
            SimulationIssue(
                fingerprint=_fingerprint(
                    check_id=check_id,
                    issue_type=spec.issue_type,
                    code=spec.code,
                ),
                issue_type=spec.issue_type,
                check_id=check_id,
                failure=failure,
            )
        )

    return SimulationIssueSet(
        project_id=result.project_id,
        requirement_version=result.requirement_version,
        source_artifacts=result.source_artifacts,
        runtime_run_id=result.runtime_run_id,
        mission_id=result.mission_id,
        scenario_id=result.scenario_id,
        verification_sha256=result.verification_sha256,
        issues=tuple(issues),
    )
