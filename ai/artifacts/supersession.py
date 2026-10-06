from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from ai.artifacts.lineage import ArtifactVersionRef
from ai.artifacts.versions import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactVersionConflictError,
    initialize_artifact_registry,
    verify_artifact_registry,
)
from ai.contracts.artifact_states import (
    ArtifactState,
    ArtifactStateError,
    HISTORICAL_ARTIFACT_STATES,
    InvalidArtifactStateTransition,
    parse_artifact_state,
    require_artifact_transition,
)
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import (
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


ARTIFACT_SUPERSESSION_CONTRACT_VERSION = "1.0"
MAX_AUTO_SUPERSEDED_VERSIONS = 4096


class ArtifactSupersessionError(RuntimeError):
    """Base error for deterministic A4.7 supersession handling."""


class ArtifactSupersessionConflictError(ArtifactSupersessionError):
    """Raised when versions/lifecycle prevent safe supersession."""


class ArtifactSupersessionNotFoundError(ArtifactSupersessionError):
    """Raised when the requested artifact version does not exist."""


@dataclass(frozen=True)
class ArtifactSupersessionResult:
    replacement: ArtifactVersionRef
    superseded: tuple[ArtifactVersionRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.replacement, ArtifactVersionRef):
            raise TypeError("replacement must be ArtifactVersionRef")
        if not isinstance(self.superseded, tuple):
            raise TypeError("superseded must be tuple")
        if any(not isinstance(item, ArtifactVersionRef) for item in self.superseded):
            raise TypeError("superseded entries must be ArtifactVersionRef")


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactSupersessionConflictError(
            "project_id must be canonical"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise ArtifactSupersessionConflictError(
            "project_id must identify a project"
        )
    return value


def _parse_artifact_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactSupersessionConflictError(
            "artifact_id must be canonical"
        ) from exc
    if value.kind is not IdKind.ARTIFACT:
        raise ArtifactSupersessionConflictError(
            "artifact_id must identify an artifact"
        )
    return value


def _require_version(raw: int) -> int:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, int)
        or not 1 <= raw <= 999_999
    ):
        raise ArtifactSupersessionNotFoundError(
            "artifact version is outside the supported range"
        )
    return raw


def _auto_supersede_lower_versions(
    connection: sqlite3.Connection,
    *,
    project_id: CanonicalId,
    artifact_id: CanonicalId,
    replacement_version: int,
) -> tuple[ArtifactVersionRef, ...]:
    """Mark lower active versions SUPERSEDED inside the caller transaction.

    The replacement version must already be transitioning to APPROVED in the
    same registry transaction. Historical lower versions are preserved.

    This operation records only same-logical-artifact replacement. It does not
    infer semantic invalidation of downstream lineage; A4.6 remains the owner
    of explicit STALE propagation.
    """

    higher_approved = connection.execute(
        """
        SELECT l.version
        FROM artifact_lifecycle l
        JOIN artifact_versions v
          ON v.artifact_id=l.artifact_id
         AND v.version=l.version
        WHERE l.artifact_id=?
          AND l.version>?
          AND l.status='APPROVED'
          AND v.project_id=?
        ORDER BY l.version
        LIMIT 1
        """,
        (
            artifact_id.value,
            replacement_version,
            project_id.value,
        ),
    ).fetchone()
    if higher_approved is not None:
        raise ArtifactSupersessionConflictError(
            "cannot approve an older artifact version while a higher version "
            f"is already APPROVED: {artifact_id.value}@v{higher_approved[0]}"
        )

    rows = connection.execute(
        """
        SELECT l.version, l.status
        FROM artifact_lifecycle l
        JOIN artifact_versions v
          ON v.artifact_id=l.artifact_id
         AND v.version=l.version
        WHERE l.artifact_id=?
          AND l.version<?
          AND v.project_id=?
        ORDER BY l.version
        LIMIT ?
        """,
        (
            artifact_id.value,
            replacement_version,
            project_id.value,
            MAX_AUTO_SUPERSEDED_VERSIONS + 1,
        ),
    ).fetchall()

    if len(rows) > MAX_AUTO_SUPERSEDED_VERSIONS:
        raise ArtifactSupersessionConflictError(
            "automatic supersession exceeds MAX_AUTO_SUPERSEDED_VERSIONS"
        )

    changed: list[ArtifactVersionRef] = []
    for version, raw_state in rows:
        try:
            current = parse_artifact_state(raw_state)
        except ArtifactStateError as exc:
            raise ArtifactSupersessionConflictError(
                "artifact lifecycle contains invalid state"
            ) from exc

        if current in HISTORICAL_ARTIFACT_STATES:
            continue

        try:
            require_artifact_transition(
                current,
                ArtifactState.SUPERSEDED,
            )
        except InvalidArtifactStateTransition as exc:
            raise ArtifactSupersessionConflictError(str(exc)) from exc

        connection.execute(
            """
            UPDATE artifact_lifecycle
            SET status='SUPERSEDED'
            WHERE artifact_id=? AND version=? AND status=?
            """,
            (
                artifact_id.value,
                version,
                current.value,
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise ArtifactSupersessionConflictError(
                "artifact lifecycle changed during automatic supersession"
            )

        connection.execute(
            """
            INSERT INTO artifact_supersession(
                artifact_id,
                superseded_version,
                replacement_version
            ) VALUES (?, ?, ?)
            """,
            (
                artifact_id.value,
                version,
                replacement_version,
            ),
        )
        changed.append(
            ArtifactVersionRef(
                artifact_id=artifact_id,
                version=version,
            )
        )

    return tuple(changed)


class ArtifactSupersessionStore:
    """Read A4.7 replacement/currentness metadata.

    Automatic writes occur transactionally when a newer artifact version enters
    APPROVED through ArtifactLifecycleStore. The read API is deliberately
    bounded and does not perform lifecycle mutation itself.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_registry(
        self,
        project_id: CanonicalId | str,
    ) -> tuple[CanonicalId, sqlite3.Connection]:
        project = _parse_project_id(project_id)
        try:
            handle = self._project_store.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ArtifactSupersessionConflictError(
                f"project could not be opened: {exc}"
            ) from exc

        path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
        if path.is_symlink():
            raise ArtifactSupersessionConflictError(
                "artifact registry must not be a symbolic link"
            )
        if not path.exists():
            raise ArtifactSupersessionNotFoundError(
                "artifact registry does not exist"
            )
        try:
            initialize_artifact_registry(path)
            connection = sqlite3.connect(path)
            verify_artifact_registry(connection)
        except ArtifactVersionConflictError as exc:
            raise ArtifactSupersessionConflictError(
                f"artifact registry is invalid: {exc}"
            ) from exc
        return handle.project_id, connection

    def current_approved(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
    ) -> ArtifactVersionRef | None:
        artifact = _parse_artifact_id(artifact_id)
        project, connection = self._open_registry(project_id)
        try:
            rows = connection.execute(
                """
                SELECT l.version
                FROM artifact_lifecycle l
                JOIN artifact_versions v
                  ON v.artifact_id=l.artifact_id
                 AND v.version=l.version
                WHERE l.artifact_id=?
                  AND l.status='APPROVED'
                  AND v.project_id=?
                ORDER BY l.version
                LIMIT 2
                """,
                (artifact.value, project.value),
            ).fetchall()
            if len(rows) > 1:
                raise ArtifactSupersessionConflictError(
                    "logical artifact has multiple APPROVED versions"
                )
            if not rows:
                return None
            return ArtifactVersionRef(
                artifact_id=artifact,
                version=rows[0][0],
            )
        finally:
            connection.close()

    def replacement_for(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> ArtifactVersionRef | None:
        artifact = _parse_artifact_id(artifact_id)
        version = _require_version(version)
        _project, connection = self._open_registry(project_id)
        try:
            row = connection.execute(
                """
                SELECT replacement_version
                FROM artifact_supersession
                WHERE artifact_id=? AND superseded_version=?
                """,
                (artifact.value, version),
            ).fetchone()
            if row is None:
                return None
            return ArtifactVersionRef(
                artifact_id=artifact,
                version=row[0],
            )
        finally:
            connection.close()

    def superseded_by(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        replacement_version: int,
    ) -> tuple[ArtifactVersionRef, ...]:
        artifact = _parse_artifact_id(artifact_id)
        replacement_version = _require_version(replacement_version)
        _project, connection = self._open_registry(project_id)
        try:
            rows = connection.execute(
                """
                SELECT superseded_version
                FROM artifact_supersession
                WHERE artifact_id=? AND replacement_version=?
                ORDER BY superseded_version
                LIMIT ?
                """,
                (
                    artifact.value,
                    replacement_version,
                    MAX_AUTO_SUPERSEDED_VERSIONS + 1,
                ),
            ).fetchall()
            if len(rows) > MAX_AUTO_SUPERSEDED_VERSIONS:
                raise ArtifactSupersessionConflictError(
                    "superseded-by result exceeds bounded limit"
                )
            return tuple(
                ArtifactVersionRef(
                    artifact_id=artifact,
                    version=row[0],
                )
                for row in rows
            )
        finally:
            connection.close()
