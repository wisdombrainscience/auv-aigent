from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.project.requirement_versions import (
    RequirementVersionConflictError,
    RequirementVersionNotFoundError,
    RequirementVersionStore,
)
from ai.project.store import (
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)
from ai.uav_bridge.proposal import SimulationProposal
from orchestration.mission_experiment import MissionExperimentResult
from src.evidence.attempt import AttemptEvidence
from src.runtime_paths import runs_root


UAV_RUN_LINK_CONTRACT_VERSION = "1.0"
UAV_RUN_LINK_REGISTRY_SCHEMA_VERSION = 1
UAV_RUN_LINK_REGISTRY_DB_FILENAME = "uav_run_links.sqlite3"
MAX_UAV_RUN_LINK_RECORD_JSON_BYTES = 2 * 1024 * 1024
MAX_UAV_RUN_EVIDENCE_JSON_BYTES = 8 * 1024 * 1024
MAX_UAV_RUN_ID_CHARS = 160

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_ATTEMPT_NAME_RE = re.compile(r"^attempt_([0-9]{3,6})$")

_EXPECTED_TABLES = (
    "uav_run_link_registry_meta",
    "uav_run_links",
)

_UAV_RUN_LINK_KEYS = frozenset(
    {
        "contract_version",
        "project_id",
        "requirement_version",
        "source_artifacts",
        "proposal_sha256",
        "scenario_id",
        "mission_id",
        "runtime_run_id",
        "runtime_result_sha256",
        "attempt_relpath",
        "finalized_sha256",
        "mission_input_sha256",
        "mission_steps_sha256",
        "verification_sha256",
        "simulation_provenance",
        "verifier_provenance",
    }
)

_UAV_RUN_LINK_DDL = (
    """
    CREATE TABLE uav_run_link_registry_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1)
    )
    """.strip(),
    """
    CREATE TABLE uav_run_links (
        runtime_run_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        CHECK (length(runtime_run_id) >= 1 AND length(runtime_run_id) <= 160),
        CHECK (
            project_id GLOB
            'UAV-PROJ-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (project_id <> 'UAV-PROJ-000000'),
        CHECK (length(scenario_id) >= 1 AND length(scenario_id) <= 128),
        CHECK (length(mission_id) >= 1 AND length(mission_id) <= 64)
    )
    """.strip(),
)


class UAVRunLinkError(RuntimeError):
    """Base error for U11.7 project/UAV-run linkage."""


class UAVRunLinkConflictError(UAVRunLinkError):
    """Raised when linkage state or runtime evidence is conflicting/corrupt."""


class UAVRunLinkNotFoundError(UAVRunLinkError):
    """Raised when a requested runtime run link does not exist."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_bytes(value: Any, *, label: str, limit: int) -> bytes:
    try:
        raw = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UAVRunLinkConflictError(
            f"{label} is not strict finite JSON"
        ) from exc
    if len(raw) > limit:
        raise UAVRunLinkConflictError(f"{label} exceeds bounded size limit")
    return raw


def _reject_constant(raw: str) -> None:
    raise UAVRunLinkConflictError(
        f"evidence contains non-finite JSON constant: {raw}"
    )


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UAVRunLinkConflictError(
                f"evidence contains duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _validate_finite_json(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UAVRunLinkConflictError(
                f"evidence contains non-finite number at {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise UAVRunLinkConflictError(
                    f"evidence key at {path} must be text"
                )
            _validate_finite_json(item, path=f"{path}.{key}")
        return
    raise UAVRunLinkConflictError(
        f"evidence contains unsupported JSON value at {path}"
    )


def _read_strict_json(path: Path, *, label: str) -> Any:
    if path.is_symlink():
        raise UAVRunLinkConflictError(f"{label} must not be a symbolic link")
    if not path.is_file():
        raise UAVRunLinkConflictError(f"{label} is missing")
    raw = path.read_bytes()
    if len(raw) > MAX_UAV_RUN_EVIDENCE_JSON_BYTES:
        raise UAVRunLinkConflictError(f"{label} exceeds bounded size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UAVRunLinkConflictError(f"{label} is not valid UTF-8") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except UAVRunLinkConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise UAVRunLinkConflictError(f"{label} is not valid JSON") from exc
    _validate_finite_json(payload)
    return payload


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise UAVRunLinkConflictError(
            "project_id must be a canonical PROJECT ID"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise UAVRunLinkConflictError(
            "project_id must identify a project"
        )
    return value


def _clean_run_id(raw: Any) -> str:
    if (
        not isinstance(raw, str)
        or len(raw) > MAX_UAV_RUN_ID_CHARS
        or _RUN_ID_RE.fullmatch(raw) is None
    ):
        raise UAVRunLinkConflictError(
            "runtime_run_id must be one bounded clean identifier"
        )
    return raw


def _clean_sha256(raw: Any, *, field: str) -> str:
    if (
        not isinstance(raw, str)
        or re.fullmatch(r"[0-9a-f]{64}", raw) is None
    ):
        raise UAVRunLinkConflictError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    return raw


def _source_artifacts(
    raw: Any,
) -> tuple[ArtifactInputVersion, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise UAVRunLinkConflictError(
            "source_artifacts must be a non-empty array"
        )
    values: list[ArtifactInputVersion] = []
    seen: set[tuple[str, int]] = set()
    for item in raw:
        try:
            ref = (
                item
                if isinstance(item, ArtifactInputVersion)
                else ArtifactInputVersion.from_dict(item)
            )
        except Exception as exc:
            raise UAVRunLinkConflictError(
                "source_artifacts contains an invalid artifact version"
            ) from exc
        if ref.entity_id.kind is not IdKind.ARTIFACT:
            raise UAVRunLinkConflictError(
                "source_artifacts may reference only artifacts"
            )
        key = (ref.entity_id.value, ref.version)
        if key in seen:
            raise UAVRunLinkConflictError(
                "source_artifacts contains a duplicate"
            )
        seen.add(key)
        values.append(ref)
    return tuple(values)


@dataclass(frozen=True)
class UAVRunLinkRecord:
    """Immutable project-local binding to one finalized existing UAV runtime run.

    The record intentionally does not interpret the verifier result. It binds the
    exact proposal lineage to immutable runtime evidence so Phase 12 can parse
    verifier output later without the bridge inventing a second verdict.
    """

    project_id: CanonicalId
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    proposal_sha256: str
    scenario_id: str
    mission_id: str
    runtime_run_id: str
    runtime_result_sha256: str
    attempt_relpath: str
    finalized_sha256: str
    mission_input_sha256: str
    mission_steps_sha256: str
    verification_sha256: str
    simulation_provenance: ProvenanceClass = ProvenanceClass.SIMULATION_RESULT
    verifier_provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        project_id = _parse_project_id(self.project_id)
        if (
            isinstance(self.requirement_version, bool)
            or not isinstance(self.requirement_version, int)
            or not 1 <= self.requirement_version <= 999_999
        ):
            raise UAVRunLinkConflictError(
                "requirement_version is invalid"
            )
        source_artifacts = _source_artifacts(self.source_artifacts)
        scenario_id = self.scenario_id
        if (
            not isinstance(scenario_id, str)
            or not scenario_id
            or scenario_id != scenario_id.strip()
            or "/" in scenario_id
            or "\\" in scenario_id
        ):
            raise UAVRunLinkConflictError(
                "scenario_id must remain one safe path segment"
            )
        mission_id = self.mission_id
        if (
            not isinstance(mission_id, str)
            or not mission_id
            or len(mission_id) > 64
        ):
            raise UAVRunLinkConflictError("mission_id is invalid")
        runtime_run_id = _clean_run_id(self.runtime_run_id)
        attempt_relpath = self.attempt_relpath
        if (
            not isinstance(attempt_relpath, str)
            or not attempt_relpath
            or Path(attempt_relpath).is_absolute()
            or ".." in Path(attempt_relpath).parts
        ):
            raise UAVRunLinkConflictError(
                "attempt_relpath must be a safe relative path"
            )
        try:
            simulation_provenance = ProvenanceClass(
                self.simulation_provenance
            )
            verifier_provenance = ProvenanceClass(
                self.verifier_provenance
            )
        except (TypeError, ValueError) as exc:
            raise UAVRunLinkConflictError(
                "provenance class is invalid"
            ) from exc
        if simulation_provenance is not ProvenanceClass.SIMULATION_RESULT:
            raise UAVRunLinkConflictError(
                "simulation_provenance must be SIMULATION_RESULT"
            )
        if verifier_provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise UAVRunLinkConflictError(
                "verifier_provenance must be VERIFIER_RESULT"
            )

        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "source_artifacts", source_artifacts)
        object.__setattr__(self, "runtime_run_id", runtime_run_id)
        object.__setattr__(self, "simulation_provenance", simulation_provenance)
        object.__setattr__(self, "verifier_provenance", verifier_provenance)

        for field in (
            "proposal_sha256",
            "runtime_result_sha256",
            "finalized_sha256",
            "mission_input_sha256",
            "mission_steps_sha256",
            "verification_sha256",
        ):
            object.__setattr__(
                self,
                field,
                _clean_sha256(getattr(self, field), field=field),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": UAV_RUN_LINK_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [
                item.to_dict() for item in self.source_artifacts
            ],
            "proposal_sha256": self.proposal_sha256,
            "scenario_id": self.scenario_id,
            "mission_id": self.mission_id,
            "runtime_run_id": self.runtime_run_id,
            "runtime_result_sha256": self.runtime_result_sha256,
            "attempt_relpath": self.attempt_relpath,
            "finalized_sha256": self.finalized_sha256,
            "mission_input_sha256": self.mission_input_sha256,
            "mission_steps_sha256": self.mission_steps_sha256,
            "verification_sha256": self.verification_sha256,
            "simulation_provenance": self.simulation_provenance.value,
            "verifier_provenance": self.verifier_provenance.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "UAVRunLinkRecord":
        if not isinstance(raw, Mapping):
            raise UAVRunLinkConflictError(
                "UAV run link record must be an object"
            )
        if frozenset(raw.keys()) != _UAV_RUN_LINK_KEYS:
            raise UAVRunLinkConflictError(
                "UAV run link record keys do not match U11.7 contract"
            )
        if raw["contract_version"] != UAV_RUN_LINK_CONTRACT_VERSION:
            raise UAVRunLinkConflictError(
                "UAV run link contract version mismatch"
            )
        return cls(
            project_id=raw["project_id"],
            requirement_version=raw["requirement_version"],
            source_artifacts=tuple(raw["source_artifacts"]),
            proposal_sha256=raw["proposal_sha256"],
            scenario_id=raw["scenario_id"],
            mission_id=raw["mission_id"],
            runtime_run_id=raw["runtime_run_id"],
            runtime_result_sha256=raw["runtime_result_sha256"],
            attempt_relpath=raw["attempt_relpath"],
            finalized_sha256=raw["finalized_sha256"],
            mission_input_sha256=raw["mission_input_sha256"],
            mission_steps_sha256=raw["mission_steps_sha256"],
            verification_sha256=raw["verification_sha256"],
            simulation_provenance=raw["simulation_provenance"],
            verifier_provenance=raw["verifier_provenance"],
        )


def _registry_path(project_store: ProjectStore, project_id: CanonicalId) -> Path:
    try:
        handle = project_store.open_project(project_id)
    except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
        raise UAVRunLinkConflictError(
            f"project could not be opened: {exc}"
        ) from exc
    simulation_dir = handle.layout.data_directories["simulation"]
    if simulation_dir.is_symlink() or not simulation_dir.is_dir():
        raise UAVRunLinkConflictError(
            "project simulation directory is unsafe"
        )
    path = simulation_dir / UAV_RUN_LINK_REGISTRY_DB_FILENAME
    if path.is_symlink():
        raise UAVRunLinkConflictError(
            "UAV run link registry must not be a symbolic link"
        )
    return path


def initialize_uav_run_link_registry(path: Path) -> None:
    path = Path(path)
    if path.is_symlink():
        raise UAVRunLinkConflictError(
            "UAV run link registry must not be a symbolic link"
        )
    path.parent.mkdir(parents=False, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        )
        if not tables:
            for ddl in _UAV_RUN_LINK_DDL:
                connection.execute(ddl)
            connection.execute(
                """
                INSERT INTO uav_run_link_registry_meta(singleton, schema_version)
                VALUES(1, ?)
                """,
                (UAV_RUN_LINK_REGISTRY_SCHEMA_VERSION,),
            )
            connection.commit()
        verify_uav_run_link_registry(connection)
    finally:
        connection.close()


def verify_uav_run_link_registry(connection: sqlite3.Connection) -> None:
    tables = tuple(
        row[0]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    )
    if tables != tuple(sorted(_EXPECTED_TABLES)):
        raise UAVRunLinkConflictError(
            "UAV run link registry table set is invalid"
        )
    row = connection.execute(
        """
        SELECT schema_version
        FROM uav_run_link_registry_meta
        WHERE singleton=1
        """
    ).fetchone()
    if row != (UAV_RUN_LINK_REGISTRY_SCHEMA_VERSION,):
        raise UAVRunLinkConflictError(
            "UAV run link registry schema version is invalid"
        )



def _verify_project_lineage(
    project_store: ProjectStore,
    proposal: SimulationProposal,
) -> None:
    """Require the proposal's historical requirement/artifact lineage to exist.

    U11.7 deliberately does not require those versions to remain current or
    APPROVED: a completed runtime run is historical evidence and must remain
    linkable even if engineering state changes after execution. Existence and
    project identity are mandatory; lifecycle interpretation belongs elsewhere.
    """

    requirements = RequirementVersionStore(project_store)
    try:
        requirements.open_version(
            proposal.project_id,
            proposal.requirement_version,
        )
    except (
        RequirementVersionNotFoundError,
        RequirementVersionConflictError,
    ) as exc:
        raise UAVRunLinkConflictError(
            "proposal requirement_version does not exist in Project State"
        ) from exc

    artifacts = ArtifactVersionStore(project_store)
    for ref in proposal.source_artifacts:
        try:
            record = artifacts.open_version(
                proposal.project_id,
                ref.entity_id,
                ref.version,
            )
        except (
            ArtifactVersionNotFoundError,
            ArtifactVersionConflictError,
        ) as exc:
            raise UAVRunLinkConflictError(
                "proposal source artifact version does not exist in Project State: "
                f"{ref.entity_id.value}@v{ref.version}"
            ) from exc
        if record.project_id != proposal.project_id:
            raise UAVRunLinkConflictError(
                "proposal source artifact belongs to a different project"
            )

def _attempt_binding(
    deployment_root: Path,
    proposal: SimulationProposal,
    result: MissionExperimentResult,
) -> tuple[str, Path, int]:
    root = runs_root(Path(deployment_root).resolve()).resolve()
    attempt = Path(result.attempt_dir).resolve()
    try:
        relative = attempt.relative_to(root)
    except ValueError as exc:
        raise UAVRunLinkConflictError(
            "runtime attempt is outside the existing runs root"
        ) from exc

    parts = relative.parts
    if len(parts) != 3:
        raise UAVRunLinkConflictError(
            "runtime attempt path does not match mission/scenario/attempt layout"
        )
    mission_id, scenario_id, attempt_name = parts
    if mission_id != proposal.mission_id or scenario_id != proposal.scenario_id:
        raise UAVRunLinkConflictError(
            "runtime attempt path does not match proposal mission/scenario"
        )
    match = _ATTEMPT_NAME_RE.fullmatch(attempt_name)
    if match is None:
        raise UAVRunLinkConflictError(
            "runtime attempt directory name is invalid"
        )
    attempt_number = int(match.group(1))
    if attempt_number <= 0:
        raise UAVRunLinkConflictError(
            "runtime attempt number must be positive"
        )

    # Reject symlinked evidence components. A finalized link must identify real
    # immutable runtime evidence, not an alias that can be retargeted later.
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise UAVRunLinkConflictError(
                "runtime evidence path must not contain symbolic links"
            )

    if not attempt.is_dir():
        raise UAVRunLinkConflictError(
            "runtime attempt directory does not exist"
        )
    return relative.as_posix(), root, attempt_number


def _build_record(
    deployment_root: Path,
    proposal: SimulationProposal,
    result: MissionExperimentResult,
) -> UAVRunLinkRecord:
    if not isinstance(proposal, SimulationProposal):
        raise TypeError("proposal must be SimulationProposal")
    if not isinstance(result, MissionExperimentResult):
        raise TypeError("result must be MissionExperimentResult")
    if result.mission_id != proposal.mission_id:
        raise UAVRunLinkConflictError(
            "runtime result mission_id does not match proposal"
        )

    runtime_run_id = _clean_run_id(result.run_id)
    attempt_relpath, root, attempt_number = _attempt_binding(
        deployment_root,
        proposal,
        result,
    )
    evidence = AttemptEvidence(
        root=root,
        task_id=proposal.mission_id,
        scenario_id=proposal.scenario_id,
        attempt=attempt_number,
    )
    ok, problems = evidence.verify_integrity()
    if not ok:
        raise UAVRunLinkConflictError(
            "runtime evidence is not finalized and intact: "
            + ",".join(problems[:8])
        )

    attempt = Path(result.attempt_dir).resolve()
    metadata_path = attempt / "metadata.json"
    mission_input_path = attempt / "mission_input.json"
    mission_steps_path = attempt / "mission_steps.json"
    verification_path = attempt / "verification.json"
    finalized_path = attempt / "finalized.json"

    metadata = _read_strict_json(metadata_path, label="metadata.json")
    mission_input = _read_strict_json(
        mission_input_path,
        label="mission_input.json",
    )
    mission_steps = _read_strict_json(
        mission_steps_path,
        label="mission_steps.json",
    )
    verification = _read_strict_json(
        verification_path,
        label="verification.json",
    )
    _read_strict_json(finalized_path, label="finalized.json")

    if not isinstance(metadata, dict):
        raise UAVRunLinkConflictError("metadata.json must contain an object")
    expected_metadata = {
        "task_id": proposal.mission_id,
        "scenario_id": proposal.scenario_id,
        "run_id": runtime_run_id,
        "mission_id": proposal.mission_id,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise UAVRunLinkConflictError(
                f"metadata.json {key} does not match proposal/runtime result"
            )

    proposal_dict = proposal.to_dict()
    if mission_input != proposal_dict["mission_payload"]:
        raise UAVRunLinkConflictError(
            "mission_input.json does not match the authorized proposal payload"
        )

    if not isinstance(mission_steps, dict) or set(mission_steps) != {"steps"}:
        raise UAVRunLinkConflictError(
            "mission_steps.json must contain exactly the steps array"
        )
    if mission_steps["steps"] != list(result.step_results):
        raise UAVRunLinkConflictError(
            "mission_steps.json does not match MissionExperimentResult"
        )

    if verification != result.verification:
        raise UAVRunLinkConflictError(
            "verification.json does not match MissionExperimentResult"
        )

    cleanup_path = attempt / "adapter_cleanup_error.json"
    if result.adapter_cleanup_error is None:
        if cleanup_path.exists() or cleanup_path.is_symlink():
            raise UAVRunLinkConflictError(
                "unexpected adapter cleanup evidence conflicts with runtime result"
            )
    else:
        cleanup = _read_strict_json(
            cleanup_path,
            label="adapter_cleanup_error.json",
        )
        if cleanup != {"error": result.adapter_cleanup_error}:
            raise UAVRunLinkConflictError(
                "adapter cleanup evidence does not match runtime result"
            )

    proposal_raw = _canonical_json_bytes(
        proposal_dict,
        label="SimulationProposal",
        limit=MAX_UAV_RUN_LINK_RECORD_JSON_BYTES,
    )
    result_raw = _canonical_json_bytes(
        result.to_dict(),
        label="MissionExperimentResult",
        limit=MAX_UAV_RUN_LINK_RECORD_JSON_BYTES,
    )

    return UAVRunLinkRecord(
        project_id=proposal.project_id,
        requirement_version=proposal.requirement_version,
        source_artifacts=proposal.source_artifacts,
        proposal_sha256=_sha256_bytes(proposal_raw),
        scenario_id=proposal.scenario_id,
        mission_id=proposal.mission_id,
        runtime_run_id=runtime_run_id,
        runtime_result_sha256=_sha256_bytes(result_raw),
        attempt_relpath=attempt_relpath,
        finalized_sha256=_sha256_file(finalized_path),
        mission_input_sha256=_sha256_file(mission_input_path),
        mission_steps_sha256=_sha256_file(mission_steps_path),
        verification_sha256=_sha256_file(verification_path),
    )


class UAVRunLinkStore:
    """Persist immutable links from Project State lineage to finalized UAV runs."""

    def __init__(
        self,
        project_store: ProjectStore,
        deployment_root: Path,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._deployment_root = Path(deployment_root).resolve()

    def link(
        self,
        proposal: SimulationProposal,
        result: MissionExperimentResult,
    ) -> UAVRunLinkRecord:
        if not isinstance(proposal, SimulationProposal):
            raise TypeError("proposal must be SimulationProposal")
        _verify_project_lineage(self._project_store, proposal)
        record = _build_record(
            self._deployment_root,
            proposal,
            result,
        )
        path = _registry_path(self._project_store, record.project_id)
        initialize_uav_run_link_registry(path)

        raw = _canonical_json_bytes(
            record.to_dict(),
            label="UAVRunLinkRecord",
            limit=MAX_UAV_RUN_LINK_RECORD_JSON_BYTES,
        ).decode("utf-8").rstrip("\n")

        connection = sqlite3.connect(path)
        try:
            verify_uav_run_link_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT record_json
                FROM uav_run_links
                WHERE runtime_run_id=?
                """,
                (record.runtime_run_id,),
            ).fetchone()
            if row is not None:
                try:
                    existing = UAVRunLinkRecord.from_dict(
                        json.loads(
                            row[0],
                            object_pairs_hook=_object_without_duplicates,
                            parse_constant=_reject_constant,
                        )
                    )
                except Exception as exc:
                    raise UAVRunLinkConflictError(
                        "existing UAV run link record is corrupt"
                    ) from exc
                if existing != record:
                    raise UAVRunLinkConflictError(
                        "runtime_run_id is already linked with conflicting data"
                    )
                connection.rollback()
                return existing

            connection.execute(
                """
                INSERT INTO uav_run_links(
                    runtime_run_id,
                    project_id,
                    scenario_id,
                    mission_id,
                    record_json
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    record.runtime_run_id,
                    record.project_id.value,
                    record.scenario_id,
                    record.mission_id,
                    raw,
                ),
            )
            connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise UAVRunLinkConflictError(
                "UAV run link registry rejected the record"
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def open(
        self,
        project_id: CanonicalId | str,
        runtime_run_id: str,
    ) -> UAVRunLinkRecord:
        project = _parse_project_id(project_id)
        run_id = _clean_run_id(runtime_run_id)
        path = _registry_path(self._project_store, project)
        if not path.exists():
            raise UAVRunLinkNotFoundError(
                "UAV run link registry does not exist"
            )
        initialize_uav_run_link_registry(path)

        connection = sqlite3.connect(path)
        try:
            verify_uav_run_link_registry(connection)
            row = connection.execute(
                """
                SELECT record_json
                FROM uav_run_links
                WHERE runtime_run_id=? AND project_id=?
                """,
                (run_id, project.value),
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            raise UAVRunLinkNotFoundError(
                f"UAV run link does not exist: {run_id}"
            )
        try:
            payload = json.loads(
                row[0],
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
            return UAVRunLinkRecord.from_dict(payload)
        except Exception as exc:
            raise UAVRunLinkConflictError(
                "stored UAV run link record is corrupt"
            ) from exc


def link_uav_run_to_project(
    project_store: ProjectStore,
    deployment_root: Path,
    proposal: SimulationProposal,
    result: MissionExperimentResult,
) -> UAVRunLinkRecord:
    """Persist one finalized runtime result without interpreting its verdict."""

    return UAVRunLinkStore(project_store, deployment_root).link(
        proposal,
        result,
    )
