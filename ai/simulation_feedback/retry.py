from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion
from ai.artifacts.supersession import (
    ArtifactSupersessionConflictError,
    ArtifactSupersessionNotFoundError,
    ArtifactSupersessionStore,
)
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.simulation_feedback.rework import SimulationReworkTaskGenerationResult
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError
from ai.uav_bridge.linkage import (
    UAVRunLinkConflictError,
    UAVRunLinkNotFoundError,
    UAVRunLinkRecord,
    UAVRunLinkStore,
    link_uav_run_to_project,
)
from ai.uav_bridge.mapping import ProjectMissionMappingError, map_project_to_mission
from ai.uav_bridge.proposal import SimulationProposal
from ai.uav_bridge.runtime import invoke_existing_runtime


SIMULATION_RETRY_CONTRACT_VERSION = "1.0"
MAX_SIMULATION_RETRY_ATTEMPTS = 2
SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION = 1
SIMULATION_RETRY_REGISTRY_DB_FILENAME = "simulation_retries.sqlite3"
MAX_SIMULATION_RETRY_REQUEST_JSON_BYTES = 2 * 1024 * 1024

_RETRY_STATES = frozenset({"RESERVED", "LINKED", "FAILED"})
_EXPECTED_TABLES = (
    "simulation_retry_attempts",
    "simulation_retry_registry_meta",
)


class SimulationRetryError(RuntimeError):
    """Base error for deterministic F12.6 bounded simulation retry."""


class SimulationRetryConflictError(SimulationRetryError):
    """Raised when retry preconditions or persisted retry state conflict."""


class SimulationRetryBudgetExhaustedError(SimulationRetryError):
    """Raised when the frozen two-retry simulation budget is exhausted."""


@dataclass(frozen=True)
class SimulationRetryResult:
    """One linked retry run without reinterpreting the verifier verdict."""

    project_id: CanonicalId
    root_runtime_run_id: str
    parent_runtime_run_id: str
    retry_attempt: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    runtime_run_id: str
    verification_sha256: str
    reused_existing: bool
    simulation_provenance: ProvenanceClass = ProvenanceClass.SIMULATION_RESULT
    verifier_provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise SimulationRetryConflictError("project_id must identify a project")
        for field in ("root_runtime_run_id", "parent_runtime_run_id", "runtime_run_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise SimulationRetryConflictError(f"{field} must be clean non-empty text")
        if (
            isinstance(self.retry_attempt, bool)
            or not isinstance(self.retry_attempt, int)
            or not 1 <= self.retry_attempt <= MAX_SIMULATION_RETRY_ATTEMPTS
        ):
            raise SimulationRetryConflictError("retry_attempt is outside the frozen retry budget")
        if not isinstance(self.source_artifacts, tuple) or not self.source_artifacts:
            raise SimulationRetryConflictError("source_artifacts must be a non-empty tuple")
        if any(not isinstance(item, ArtifactInputVersion) for item in self.source_artifacts):
            raise SimulationRetryConflictError("source_artifacts must contain ArtifactInputVersion values")
        if (
            not isinstance(self.verification_sha256, str)
            or len(self.verification_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.verification_sha256)
        ):
            raise SimulationRetryConflictError("verification_sha256 must be lowercase sha256")
        if not isinstance(self.reused_existing, bool):
            raise SimulationRetryConflictError("reused_existing must be boolean")
        if self.simulation_provenance is not ProvenanceClass.SIMULATION_RESULT:
            raise SimulationRetryConflictError("simulation provenance must remain SIMULATION_RESULT")
        if self.verifier_provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationRetryConflictError("verifier provenance must remain VERIFIER_RESULT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": SIMULATION_RETRY_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "root_runtime_run_id": self.root_runtime_run_id,
            "parent_runtime_run_id": self.parent_runtime_run_id,
            "retry_attempt": self.retry_attempt,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "runtime_run_id": self.runtime_run_id,
            "verification_sha256": self.verification_sha256,
            "reused_existing": self.reused_existing,
            "simulation_provenance": self.simulation_provenance.value,
            "verifier_provenance": self.verifier_provenance.value,
        }


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SimulationRetryConflictError("retry request is not canonical JSON") from exc
    if len(raw) > MAX_SIMULATION_RETRY_REQUEST_JSON_BYTES:
        raise SimulationRetryConflictError("retry request exceeds bounded JSON size")
    return raw


def _registry_path(projects: ProjectStore, project_id: CanonicalId) -> Path:
    try:
        handle = projects.open_project(project_id)
    except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
        raise SimulationRetryConflictError("project could not be opened safely") from exc
    simulation_dir = handle.layout.data_directories["simulation"]
    if simulation_dir.is_symlink():
        raise SimulationRetryConflictError("simulation directory must not be a symbolic link")
    simulation_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = simulation_dir / SIMULATION_RETRY_REGISTRY_DB_FILENAME
    if path.is_symlink():
        raise SimulationRetryConflictError("simulation retry registry must not be a symbolic link")
    return path


def initialize_simulation_retry_registry(path: Path) -> None:
    path = Path(path)
    if path.is_symlink():
        raise SimulationRetryConflictError("simulation retry registry must not be a symbolic link")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_retry_registry_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_retry_attempts (
                root_runtime_run_id TEXT NOT NULL,
                retry_attempt INTEGER NOT NULL CHECK (retry_attempt BETWEEN 1 AND 2),
                parent_runtime_run_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
                proposal_sha256 TEXT NOT NULL CHECK (length(proposal_sha256) = 64),
                state TEXT NOT NULL CHECK (state IN ('RESERVED','LINKED','FAILED')),
                runtime_run_id TEXT UNIQUE,
                verification_sha256 TEXT,
                PRIMARY KEY (root_runtime_run_id, retry_attempt),
                CHECK (
                    (state = 'LINKED' AND runtime_run_id IS NOT NULL AND verification_sha256 IS NOT NULL)
                    OR
                    (state <> 'LINKED' AND runtime_run_id IS NULL AND verification_sha256 IS NULL)
                )
            )
            """
        )
        row = connection.execute(
            "SELECT schema_version FROM simulation_retry_registry_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO simulation_retry_registry_meta(singleton,schema_version) VALUES(1,?)",
                (SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION,),
            )
        elif row[0] != SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION:
            raise SimulationRetryConflictError("simulation retry registry schema version mismatch")
        connection.commit()
    finally:
        connection.close()


def verify_simulation_retry_registry(connection: sqlite3.Connection) -> None:
    tables = tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    )
    if tables != _EXPECTED_TABLES:
        raise SimulationRetryConflictError("simulation retry registry table set is invalid")
    row = connection.execute(
        "SELECT schema_version FROM simulation_retry_registry_meta WHERE singleton=1"
    ).fetchone()
    if row != (SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION,):
        raise SimulationRetryConflictError("simulation retry registry metadata is invalid")
    bad = connection.execute(
        """
        SELECT root_runtime_run_id, retry_attempt
        FROM simulation_retry_attempts
        WHERE state NOT IN ('RESERVED','LINKED','FAILED')
           OR retry_attempt < 1 OR retry_attempt > 2
           OR length(request_sha256) <> 64
           OR length(proposal_sha256) <> 64
           OR (state='LINKED' AND (runtime_run_id IS NULL OR verification_sha256 IS NULL))
           OR (state<>'LINKED' AND (runtime_run_id IS NOT NULL OR verification_sha256 IS NOT NULL))
        LIMIT 1
        """
    ).fetchone()
    if bad is not None:
        raise SimulationRetryConflictError("simulation retry registry contains invalid attempt state")


def _validate_parent_link(
    links: UAVRunLinkStore,
    generation: SimulationReworkTaskGenerationResult,
) -> UAVRunLinkRecord:
    try:
        record = links.open(generation.project_id, generation.runtime_run_id)
    except (UAVRunLinkNotFoundError, UAVRunLinkConflictError) as exc:
        raise SimulationRetryConflictError("parent UAV run link is unavailable or corrupt") from exc
    expected = {
        "project_id": generation.project_id,
        "requirement_version": generation.requirement_version,
        "source_artifacts": generation.source_artifacts,
        "runtime_run_id": generation.runtime_run_id,
        "mission_id": generation.mission_id,
        "scenario_id": generation.scenario_id,
        "verification_sha256": generation.verification_sha256,
    }
    for field, value in expected.items():
        if getattr(record, field) != value:
            raise SimulationRetryConflictError(f"F12.5/U11.7 parent lineage mismatch: {field}")
    return record


def _validate_rework_tasks_done(
    projects: ProjectStore,
    generation: SimulationReworkTaskGenerationResult,
) -> tuple[CanonicalId, ...]:
    if not generation.bindings:
        raise SimulationRetryConflictError("PASS feedback has no rework task and cannot be retried")
    tasks = TaskStore(projects)
    task_ids: list[CanonicalId] = []
    for binding in generation.bindings:
        try:
            current = tasks.open_task(generation.project_id, binding.task.task_id)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise SimulationRetryConflictError("rework task is unavailable or corrupt") from exc
        frozen = binding.task
        for field in (
            "task_id",
            "project_id",
            "title",
            "role",
            "priority",
            "dependencies",
            "input_artifacts",
            "output_schema",
            "validators",
            "review_required",
            "max_attempts",
        ):
            if getattr(current, field) != getattr(frozen, field):
                raise SimulationRetryConflictError(f"rework task contract changed after F12.5: {field}")
        if current.status is not TaskState.DONE:
            raise SimulationRetryConflictError("all F12.5 rework tasks must be DONE before retry")
        if not 1 <= current.attempt <= current.max_attempts:
            raise SimulationRetryConflictError("DONE rework task has invalid execution attempt")
        task_ids.append(current.task_id)
    if len(set(task_ids)) != len(task_ids):
        raise SimulationRetryConflictError("F12.5 rework task bindings contain duplicate task IDs")
    return tuple(task_ids)


def _replacement_sources(
    projects: ProjectStore,
    generation: SimulationReworkTaskGenerationResult,
    task_ids: tuple[CanonicalId, ...],
) -> tuple[ArtifactInputVersion, ...]:
    versions = ArtifactVersionStore(projects)
    supersession = ArtifactSupersessionStore(projects)
    allowed_producers = frozenset(task_ids)
    replacements: list[ArtifactInputVersion] = []
    for original in generation.source_artifacts:
        try:
            historical = versions.open_version(
                generation.project_id,
                original.entity_id,
                original.version,
            )
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise SimulationRetryConflictError("original simulation source artifact is unavailable") from exc
        if historical.status is not ArtifactState.STALE:
            raise SimulationRetryConflictError(
                "F12.4 source artifact must remain STALE before retry replacement"
            )
        try:
            current_ref = supersession.current_approved(
                generation.project_id,
                original.entity_id,
            )
        except (ArtifactSupersessionNotFoundError, ArtifactSupersessionConflictError) as exc:
            raise SimulationRetryConflictError("current approved replacement could not be established") from exc
        if current_ref is None or current_ref.version <= original.version:
            raise SimulationRetryConflictError(
                "each stale simulation source requires a newer current APPROVED version"
            )
        try:
            current = versions.open_version(
                generation.project_id,
                current_ref.artifact_id,
                current_ref.version,
            )
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise SimulationRetryConflictError("replacement artifact is unavailable") from exc
        if current.status is not ArtifactState.APPROVED:
            raise SimulationRetryConflictError("replacement artifact must be APPROVED")
        if current.producer_task not in allowed_producers:
            raise SimulationRetryConflictError(
                "replacement artifact must be produced by an F12.5 rework task"
            )
        replacements.append(
            ArtifactInputVersion(
                entity_id=current.artifact_id,
                version=current.version,
            )
        )
    return tuple(sorted(replacements, key=lambda item: (item.entity_id.value, item.version)))


def _proposal_for_retry(
    projects: ProjectStore,
    generation: SimulationReworkTaskGenerationResult,
    replacements: tuple[ArtifactInputVersion, ...],
) -> SimulationProposal:
    try:
        proposal = map_project_to_mission(
            projects,
            generation.project_id,
            replacements,
        )
    except ProjectMissionMappingError as exc:
        raise SimulationRetryConflictError("retry proposal could not be mapped safely") from exc
    if proposal.requirement_version != generation.requirement_version:
        raise SimulationRetryConflictError(
            "requirement version changed; automatic simulation retry is forbidden"
        )
    if proposal.scenario_id != generation.scenario_id:
        raise SimulationRetryConflictError(
            "scenario_id changed; request is a new scenario, not an automatic retry"
        )
    if proposal.mission_id != generation.mission_id:
        raise SimulationRetryConflictError("mission identity changed across automatic retry")
    if proposal.source_artifacts != replacements:
        raise SimulationRetryConflictError("retry proposal source lineage mismatch")
    return proposal


def _request_sha256(
    generation: SimulationReworkTaskGenerationResult,
    task_ids: tuple[CanonicalId, ...],
    proposal: SimulationProposal,
) -> str:
    payload = {
        "project_id": generation.project_id.value,
        "parent_runtime_run_id": generation.runtime_run_id,
        "parent_verification_sha256": generation.verification_sha256,
        "rework_task_ids": [item.value for item in task_ids],
        "proposal": proposal.to_dict(),
    }
    return _sha256_bytes(_canonical_json_bytes(payload))


def _proposal_sha256(proposal: SimulationProposal) -> str:
    return _sha256_bytes(proposal.to_json().encode("utf-8"))


@dataclass(frozen=True)
class _Reservation:
    root_runtime_run_id: str
    retry_attempt: int
    request_sha256: str
    proposal_sha256: str
    existing_runtime_run_id: str | None = None
    existing_verification_sha256: str | None = None


def _reserve_retry(
    path: Path,
    *,
    parent_runtime_run_id: str,
    request_sha256: str,
    proposal_sha256: str,
) -> _Reservation:
    initialize_simulation_retry_registry(path)
    connection = sqlite3.connect(path)
    try:
        verify_simulation_retry_registry(connection)
        connection.execute("BEGIN IMMEDIATE")

        linked_same_parent = connection.execute(
            """
            SELECT root_runtime_run_id,retry_attempt,request_sha256,proposal_sha256,
                   runtime_run_id,verification_sha256
            FROM simulation_retry_attempts
            WHERE parent_runtime_run_id=? AND state='LINKED'
            ORDER BY retry_attempt DESC
            """,
            (parent_runtime_run_id,),
        ).fetchall()
        for row in linked_same_parent:
            if row[2] == request_sha256 and row[3] == proposal_sha256:
                connection.rollback()
                return _Reservation(
                    root_runtime_run_id=row[0],
                    retry_attempt=row[1],
                    request_sha256=request_sha256,
                    proposal_sha256=proposal_sha256,
                    existing_runtime_run_id=row[4],
                    existing_verification_sha256=row[5],
                )
        if linked_same_parent:
            raise SimulationRetryConflictError(
                "parent run already produced a linked retry with different retry inputs"
            )

        reserved = connection.execute(
            """
            SELECT 1 FROM simulation_retry_attempts
            WHERE parent_runtime_run_id=? AND state='RESERVED'
            LIMIT 1
            """,
            (parent_runtime_run_id,),
        ).fetchone()
        if reserved is not None:
            raise SimulationRetryConflictError(
                "parent run already has an in-progress reserved retry"
            )

        parent_row = connection.execute(
            """
            SELECT root_runtime_run_id,retry_attempt
            FROM simulation_retry_attempts
            WHERE runtime_run_id=? AND state='LINKED'
            """,
            (parent_runtime_run_id,),
        ).fetchone()
        if parent_row is not None:
            root_runtime_run_id = parent_row[0]
        else:
            roots = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT root_runtime_run_id
                    FROM simulation_retry_attempts
                    WHERE parent_runtime_run_id=?
                    """,
                    (parent_runtime_run_id,),
                ).fetchall()
            }
            if len(roots) > 1:
                raise SimulationRetryConflictError("retry registry has conflicting root chains")
            root_runtime_run_id = next(iter(roots), parent_runtime_run_id)

        max_row = connection.execute(
            """
            SELECT MAX(retry_attempt)
            FROM simulation_retry_attempts
            WHERE root_runtime_run_id=?
            """,
            (root_runtime_run_id,),
        ).fetchone()
        previous_max = 0 if max_row is None or max_row[0] is None else int(max_row[0])
        retry_attempt = previous_max + 1
        if retry_attempt > MAX_SIMULATION_RETRY_ATTEMPTS:
            raise SimulationRetryBudgetExhaustedError(
                f"simulation retry budget exhausted for root run {root_runtime_run_id}"
            )

        connection.execute(
            """
            INSERT INTO simulation_retry_attempts(
                root_runtime_run_id,retry_attempt,parent_runtime_run_id,
                request_sha256,proposal_sha256,state,runtime_run_id,verification_sha256
            ) VALUES(?,?,?,?,?,'RESERVED',NULL,NULL)
            """,
            (
                root_runtime_run_id,
                retry_attempt,
                parent_runtime_run_id,
                request_sha256,
                proposal_sha256,
            ),
        )
        connection.commit()
        return _Reservation(
            root_runtime_run_id=root_runtime_run_id,
            retry_attempt=retry_attempt,
            request_sha256=request_sha256,
            proposal_sha256=proposal_sha256,
        )
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _mark_failed(path: Path, reservation: _Reservation) -> None:
    connection = sqlite3.connect(path)
    try:
        verify_simulation_retry_registry(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE simulation_retry_attempts
            SET state='FAILED'
            WHERE root_runtime_run_id=? AND retry_attempt=? AND state='RESERVED'
              AND request_sha256=? AND proposal_sha256=?
            """,
            (
                reservation.root_runtime_run_id,
                reservation.retry_attempt,
                reservation.request_sha256,
                reservation.proposal_sha256,
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise SimulationRetryConflictError("reserved retry could not be marked FAILED")
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _mark_linked(
    path: Path,
    reservation: _Reservation,
    link: UAVRunLinkRecord,
) -> None:
    connection = sqlite3.connect(path)
    try:
        verify_simulation_retry_registry(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE simulation_retry_attempts
            SET state='LINKED',runtime_run_id=?,verification_sha256=?
            WHERE root_runtime_run_id=? AND retry_attempt=? AND state='RESERVED'
              AND request_sha256=? AND proposal_sha256=?
            """,
            (
                link.runtime_run_id,
                link.verification_sha256,
                reservation.root_runtime_run_id,
                reservation.retry_attempt,
                reservation.request_sha256,
                reservation.proposal_sha256,
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise SimulationRetryConflictError("reserved retry could not be linked atomically")
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


class BoundedSimulationRetryController:
    """Invoke at most two automatic retries through the existing UAV Bridge.

    F12.6 never instantiates a PX4/MAVSDK adapter, never interprets the verifier
    verdict, never executes an engineering task, and never transitions a task or
    project to NEEDS_HUMAN. Exhaustion is exposed as a fail-closed error for
    F12.7 to own later.
    """

    def __init__(self, project_store: ProjectStore, deployment_root: Path) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._projects = project_store
        self._deployment_root = Path(deployment_root).resolve()
        self._links = UAVRunLinkStore(project_store, self._deployment_root)

    async def retry(
        self,
        generation: SimulationReworkTaskGenerationResult,
        *,
        adapter: Any,
        step_hook: Any = None,
        step_event_hook: Any = None,
    ) -> SimulationRetryResult:
        if not isinstance(generation, SimulationReworkTaskGenerationResult):
            raise TypeError("generation must be SimulationReworkTaskGenerationResult")
        if adapter is None:
            raise TypeError("adapter must be a trusted runtime adapter instance")

        _validate_parent_link(self._links, generation)
        task_ids = _validate_rework_tasks_done(self._projects, generation)
        replacements = _replacement_sources(self._projects, generation, task_ids)
        proposal = _proposal_for_retry(self._projects, generation, replacements)
        request_sha256 = _request_sha256(generation, task_ids, proposal)
        proposal_sha256 = _proposal_sha256(proposal)
        registry = _registry_path(self._projects, generation.project_id)
        reservation = _reserve_retry(
            registry,
            parent_runtime_run_id=generation.runtime_run_id,
            request_sha256=request_sha256,
            proposal_sha256=proposal_sha256,
        )

        if reservation.existing_runtime_run_id is not None:
            try:
                existing = self._links.open(
                    generation.project_id,
                    reservation.existing_runtime_run_id,
                )
            except (UAVRunLinkNotFoundError, UAVRunLinkConflictError) as exc:
                raise SimulationRetryConflictError(
                    "persisted retry points to a missing/corrupt U11.7 link"
                ) from exc
            if (
                existing.project_id != generation.project_id
                or existing.requirement_version != generation.requirement_version
                or existing.source_artifacts != replacements
                or existing.scenario_id != generation.scenario_id
                or existing.mission_id != generation.mission_id
                or existing.verification_sha256 != reservation.existing_verification_sha256
            ):
                raise SimulationRetryConflictError("persisted linked retry no longer matches its lineage")
            return SimulationRetryResult(
                project_id=generation.project_id,
                root_runtime_run_id=reservation.root_runtime_run_id,
                parent_runtime_run_id=generation.runtime_run_id,
                retry_attempt=reservation.retry_attempt,
                source_artifacts=existing.source_artifacts,
                runtime_run_id=existing.runtime_run_id,
                verification_sha256=existing.verification_sha256,
                reused_existing=True,
            )

        try:
            runtime_result = await invoke_existing_runtime(
                self._deployment_root,
                proposal,
                adapter=adapter,
                step_hook=step_hook,
                step_event_hook=step_event_hook,
            )
            link = link_uav_run_to_project(
                self._projects,
                self._deployment_root,
                proposal,
                runtime_result,
            )
            if (
                link.project_id != generation.project_id
                or link.requirement_version != generation.requirement_version
                or link.source_artifacts != replacements
                or link.scenario_id != generation.scenario_id
                or link.mission_id != generation.mission_id
            ):
                raise SimulationRetryConflictError("U11.7 retry link lineage mismatch")
            _mark_linked(registry, reservation, link)
        except Exception:
            try:
                _mark_failed(registry, reservation)
            except SimulationRetryConflictError:
                # Preserve the original runtime/linkage exception; a later call
                # will still fail closed because the registry is inconsistent.
                pass
            raise

        return SimulationRetryResult(
            project_id=generation.project_id,
            root_runtime_run_id=reservation.root_runtime_run_id,
            parent_runtime_run_id=generation.runtime_run_id,
            retry_attempt=reservation.retry_attempt,
            source_artifacts=link.source_artifacts,
            runtime_run_id=link.runtime_run_id,
            verification_sha256=link.verification_sha256,
            reused_existing=False,
        )
