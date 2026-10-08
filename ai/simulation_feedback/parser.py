from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.project.store import ProjectStore
from ai.uav_bridge.linkage import (
    UAVRunLinkConflictError,
    UAVRunLinkNotFoundError,
    UAVRunLinkStore,
)
from src.evidence.attempt import AttemptEvidence
from src.runtime_paths import runs_root


VERIFIER_RESULT_PARSER_CONTRACT_VERSION = "1.0"
MAX_VERIFIER_RESULT_JSON_BYTES = 256 * 1024
MAX_VERIFIER_REASON_BYTES = 4096
MAX_FAILED_STEP_IDS = 128
MAX_FAILED_STEP_ID_CHARS = 128

MISSION_VERIFIER_CHECK_IDS = (
    "step_sequence",
    "child_verdicts",
    "child_evidence_integrity",
    "continuity",
    "terminal_land_step",
    "terminal_landed_state",
    "terminal_home",
)

_VERIFICATION_KEYS = frozenset(
    {
        "overall",
        "checks",
        "max_transition_distance_m",
        "terminal_home_distance_m",
        "failed_step_ids",
        "reason",
    }
)
_STEP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ATTEMPT_RE = re.compile(r"^attempt_([0-9]{3,6})$")


class VerifierResultParserError(RuntimeError):
    """Base error for deterministic F12.1 verifier-result parsing."""


class VerifierResultEvidenceError(VerifierResultParserError):
    """Raised when linked verifier evidence is missing, altered, or unsafe."""


class VerifierResultSchemaError(VerifierResultParserError):
    """Raised when verifier output does not match the frozen mission schema."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise VerifierResultSchemaError(
            "project_id must be a canonical PROJECT ID"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise VerifierResultSchemaError("project_id must identify a project")
    return value


def _metric(raw: Any, *, field: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise VerifierResultSchemaError(f"{field} must be a finite number or null")
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise VerifierResultSchemaError(
            f"{field} must be finite and non-negative"
        )
    return value


def _parse_failed_step_ids(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise VerifierResultSchemaError("failed_step_ids must be an array")
    if len(raw) > MAX_FAILED_STEP_IDS:
        raise VerifierResultSchemaError("failed_step_ids exceeds bounded size")
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if (
            not isinstance(item, str)
            or len(item) > MAX_FAILED_STEP_ID_CHARS
            or _STEP_ID_RE.fullmatch(item) is None
        ):
            raise VerifierResultSchemaError(
                "failed_step_ids contains an invalid step identifier"
            )
        if item in seen:
            raise VerifierResultSchemaError("failed_step_ids contains a duplicate")
        seen.add(item)
        values.append(item)
    return tuple(values)


def _parse_reason(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw or raw != raw.strip() or "\x00" in raw:
        raise VerifierResultSchemaError("reason must be null or clean non-empty text")
    if len(raw.encode("utf-8")) > MAX_VERIFIER_REASON_BYTES:
        raise VerifierResultSchemaError("reason exceeds bounded size")
    return raw


def _strict_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VerifierResultEvidenceError(f"{label} is missing or unsafe")
    raw = path.read_bytes()
    if len(raw) > MAX_VERIFIER_RESULT_JSON_BYTES:
        raise VerifierResultEvidenceError(f"{label} exceeds bounded size")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerifierResultEvidenceError(f"{label} is not valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise VerifierResultSchemaError(
            f"{label} contains non-finite JSON constant: {value}"
        )

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerifierResultSchemaError(
                    f"{label} contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except VerifierResultSchemaError:
        raise
    except json.JSONDecodeError as exc:
        raise VerifierResultSchemaError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise VerifierResultSchemaError(f"{label} must contain an object")
    return value


def _safe_attempt_path(deployment_root: Path, attempt_relpath: str) -> Path:
    root = runs_root(Path(deployment_root).resolve())
    if root.is_symlink():
        raise VerifierResultEvidenceError("runs root must not be a symbolic link")
    if not root.is_dir():
        raise VerifierResultEvidenceError("runs root does not exist")

    rel = Path(attempt_relpath)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 3:
        raise VerifierResultEvidenceError("linked attempt path is not canonical")

    attempt = root / rel
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise VerifierResultEvidenceError(
                "linked attempt path contains a symbolic link"
            )
    if not attempt.is_dir():
        raise VerifierResultEvidenceError("linked attempt directory is missing")

    try:
        resolved_root = root.resolve(strict=True)
        resolved_attempt = attempt.resolve(strict=True)
        resolved_attempt.relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as exc:
        raise VerifierResultEvidenceError(
            "linked attempt escapes the existing runs root"
        ) from exc

    if resolved_attempt != attempt.absolute():
        # All components were already checked for symlinks. Keep this extra
        # canonicality guard to fail closed on unusual filesystem aliases.
        try:
            attempt.absolute().relative_to(root.absolute())
        except ValueError as exc:
            raise VerifierResultEvidenceError(
                "linked attempt is not canonical under runs root"
            ) from exc

    for path in attempt.rglob("*"):
        if path.is_symlink():
            raise VerifierResultEvidenceError(
                "linked attempt evidence contains a symbolic link"
            )
    return attempt


@dataclass(frozen=True)
class ParsedVerifierResult:
    """Immutable normalized view of one hash-bound mission verifier result.

    This contract preserves the verifier verdict and lineage. It deliberately
    does not classify an engineering issue, choose an owner, mutate lineage,
    create a rework task, authorize retry, or change task/project state.
    """

    project_id: CanonicalId
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    runtime_run_id: str
    mission_id: str
    scenario_id: str
    overall: str
    checks: Mapping[str, str]
    failed_checks: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    max_transition_distance_m: float | None
    terminal_home_distance_m: float | None
    reason: str | None
    verification_sha256: str
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        project_id = _parse_project_id(self.project_id)
        if self.overall not in {"PASS", "FAIL"}:
            raise VerifierResultSchemaError("overall must be PASS or FAIL")
        checks = dict(self.checks)
        if tuple(checks.keys()) != MISSION_VERIFIER_CHECK_IDS:
            raise VerifierResultSchemaError(
                "checks must use the canonical MISSION-001 verifier check order"
            )
        if any(value not in {"PASS", "FAIL"} for value in checks.values()):
            raise VerifierResultSchemaError("check verdict must be PASS or FAIL")
        derived_failed = tuple(
            check_id for check_id, value in checks.items() if value == "FAIL"
        )
        if self.failed_checks != derived_failed:
            raise VerifierResultSchemaError(
                "failed_checks must match canonical check verdicts"
            )
        expected_overall = "PASS" if not derived_failed else "FAIL"
        if self.overall != expected_overall:
            raise VerifierResultSchemaError(
                "overall does not match canonical verifier checks"
            )
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise VerifierResultSchemaError("provenance must be VERIFIER_RESULT")
        if not re.fullmatch(r"[0-9a-f]{64}", self.verification_sha256):
            raise VerifierResultSchemaError(
                "verification_sha256 must be a lowercase SHA-256 digest"
            )
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "checks", MappingProxyType(checks))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": VERIFIER_RESULT_PARSER_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "runtime_run_id": self.runtime_run_id,
            "mission_id": self.mission_id,
            "scenario_id": self.scenario_id,
            "overall": self.overall,
            "checks": dict(self.checks),
            "failed_checks": list(self.failed_checks),
            "failed_step_ids": list(self.failed_step_ids),
            "max_transition_distance_m": self.max_transition_distance_m,
            "terminal_home_distance_m": self.terminal_home_distance_m,
            "reason": self.reason,
            "verification_sha256": self.verification_sha256,
            "provenance": self.provenance.value,
        }


def _parse_payload(link: Any, payload: Mapping[str, Any]) -> ParsedVerifierResult:
    if frozenset(payload.keys()) != _VERIFICATION_KEYS:
        raise VerifierResultSchemaError(
            "verification result keys do not match MISSION-001 verifier contract"
        )

    overall = payload["overall"]
    if overall not in {"PASS", "FAIL"}:
        raise VerifierResultSchemaError("overall must be PASS or FAIL")

    raw_checks = payload["checks"]
    if not isinstance(raw_checks, Mapping):
        raise VerifierResultSchemaError("checks must be an object")
    if frozenset(raw_checks.keys()) != frozenset(MISSION_VERIFIER_CHECK_IDS):
        raise VerifierResultSchemaError(
            "checks do not match canonical MISSION-001 verifier checks"
        )
    checks = {check_id: raw_checks[check_id] for check_id in MISSION_VERIFIER_CHECK_IDS}
    if any(value not in {"PASS", "FAIL"} for value in checks.values()):
        raise VerifierResultSchemaError("check verdict must be PASS or FAIL")
    failed_checks = tuple(
        check_id for check_id, value in checks.items() if value == "FAIL"
    )
    expected_overall = "PASS" if not failed_checks else "FAIL"
    if overall != expected_overall:
        raise VerifierResultSchemaError(
            "overall does not match canonical verifier checks"
        )

    failed_step_ids = _parse_failed_step_ids(payload["failed_step_ids"])
    max_transition = _metric(
        payload["max_transition_distance_m"],
        field="max_transition_distance_m",
    )
    terminal_home = _metric(
        payload["terminal_home_distance_m"],
        field="terminal_home_distance_m",
    )
    reason = _parse_reason(payload["reason"])

    if overall == "PASS" and (failed_step_ids or reason is not None):
        raise VerifierResultSchemaError(
            "PASS verifier result cannot carry failed steps or failure reason"
        )

    return ParsedVerifierResult(
        project_id=link.project_id,
        requirement_version=link.requirement_version,
        source_artifacts=link.source_artifacts,
        runtime_run_id=link.runtime_run_id,
        mission_id=link.mission_id,
        scenario_id=link.scenario_id,
        overall=overall,
        checks=checks,
        failed_checks=failed_checks,
        failed_step_ids=failed_step_ids,
        max_transition_distance_m=max_transition,
        terminal_home_distance_m=terminal_home,
        reason=reason,
        verification_sha256=link.verification_sha256,
    )


def parse_linked_verifier_result(
    project_store: ProjectStore,
    deployment_root: Path,
    project_id: CanonicalId | str,
    runtime_run_id: str,
) -> ParsedVerifierResult:
    """Parse one U11.7-linked independent mission verifier result.

    F12.1 accepts only a project-local U11.7 link, revalidates the finalized
    evidence and the hash-bound verification file, then normalizes the frozen
    MISSION-001 verifier object. No state is written and no issue semantics are
    invented here.
    """

    if not isinstance(project_store, ProjectStore):
        raise TypeError("project_store must be ProjectStore")

    try:
        link = UAVRunLinkStore(project_store, deployment_root).open(
            project_id,
            runtime_run_id,
        )
    except (UAVRunLinkNotFoundError, UAVRunLinkConflictError) as exc:
        raise VerifierResultEvidenceError(
            f"U11.7 run linkage is unavailable or invalid: {exc}"
        ) from exc

    attempt = _safe_attempt_path(deployment_root, link.attempt_relpath)
    rel = Path(link.attempt_relpath)
    match = _ATTEMPT_RE.fullmatch(rel.parts[2])
    if match is None:
        raise VerifierResultEvidenceError(
            "linked attempt directory name is not canonical"
        )
    attempt_number = int(match.group(1))
    evidence = AttemptEvidence(
        root=runs_root(Path(deployment_root).resolve()),
        task_id=rel.parts[0],
        scenario_id=rel.parts[1],
        attempt=attempt_number,
    )
    if evidence.path != attempt:
        raise VerifierResultEvidenceError(
            "linked attempt path does not match evidence coordinates"
        )

    finalized = attempt / "finalized.json"
    verification = attempt / "verification.json"
    if finalized.is_symlink() or not finalized.is_file():
        raise VerifierResultEvidenceError("finalized evidence is missing or unsafe")
    if _sha256_file(finalized) != link.finalized_sha256:
        raise VerifierResultEvidenceError(
            "finalized evidence hash does not match U11.7 link"
        )

    try:
        integrity_ok, problems = evidence.verify_integrity()
    except Exception as exc:
        raise VerifierResultEvidenceError(
            "finalized evidence integrity could not be verified"
        ) from exc
    if not integrity_ok:
        detail = problems[0] if problems else "unknown"
        raise VerifierResultEvidenceError(
            f"finalized evidence integrity failed: {detail}"
        )

    if verification.is_symlink() or not verification.is_file():
        raise VerifierResultEvidenceError(
            "verification evidence is missing or unsafe"
        )
    if _sha256_file(verification) != link.verification_sha256:
        raise VerifierResultEvidenceError(
            "verification evidence hash does not match U11.7 link"
        )

    finalized_payload = _strict_json_object(finalized, label="finalized.json")
    files = finalized_payload.get("files")
    if not isinstance(files, Mapping):
        raise VerifierResultEvidenceError("finalized.json files manifest is invalid")
    if files.get("verification.json") != link.verification_sha256:
        raise VerifierResultEvidenceError(
            "finalized manifest does not bind the linked verifier hash"
        )

    payload = _strict_json_object(verification, label="verification.json")
    return _parse_payload(link, payload)
