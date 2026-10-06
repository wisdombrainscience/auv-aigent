from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from ai.artifacts.lineage import ArtifactVersionRef
from ai.artifacts.schema import ArtifactInputVersion
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
from ai.contracts.ids import (
    CanonicalId,
    CanonicalIdError,
    IdKind,
)
from ai.project.store import (
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


ARTIFACT_STALE_PROPAGATION_CONTRACT_VERSION = "1.0"
MAX_STALE_PROPAGATION_ARTIFACTS = 4096


class ArtifactStaleError(RuntimeError):
    """Base error for deterministic A4.6 STALE propagation."""


class ArtifactStaleConflictError(ArtifactStaleError):
    """Raised when lineage/state prevents safe STALE propagation."""


class ArtifactStaleNotFoundError(ArtifactStaleError):
    """Raised when a requested root artifact version does not exist."""


@dataclass(frozen=True)
class ArtifactStalePropagationResult:
    trigger: ArtifactInputVersion
    affected: tuple[ArtifactVersionRef, ...]
    changed_to_stale: tuple[ArtifactVersionRef, ...]
    already_historical: tuple[ArtifactVersionRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.trigger, ArtifactInputVersion):
            raise TypeError("trigger must be ArtifactInputVersion")
        for field_name in (
            "affected",
            "changed_to_stale",
            "already_historical",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise TypeError(f"{field_name} must be tuple")
            if any(not isinstance(item, ArtifactVersionRef) for item in value):
                raise TypeError(
                    f"{field_name} must contain ArtifactVersionRef"
                )


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactStaleConflictError(
            "project_id must be canonical"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise ArtifactStaleConflictError(
            "project_id must identify a project"
        )
    return value


class ArtifactStaleStore:
    """Propagate STALE through persisted version-specific artifact lineage.

    A4.6 deliberately separates *invalidation detection* from propagation.
    Higher deterministic logic supplies the exact dependency version that has
    become invalid. This store marks every artifact version that depends on
    that version, directly or transitively, as STALE when its current state can
    legally transition to STALE.

    REJECTED, STALE, and SUPERSEDED versions remain historical and are not
    rewritten, but traversal continues through them so downstream artifacts are
    not silently left current.

    A4.7 remains the sole owner of automatic SUPERSEDED handling.
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
            raise ArtifactStaleConflictError(
                f"project could not be opened: {exc}"
            ) from exc

        path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
        if path.is_symlink():
            raise ArtifactStaleConflictError(
                "artifact registry must not be a symbolic link"
            )
        if not path.exists():
            raise ArtifactStaleNotFoundError(
                "artifact registry does not exist"
            )

        try:
            initialize_artifact_registry(path)
            connection = sqlite3.connect(path)
            verify_artifact_registry(connection)
        except ArtifactVersionConflictError as exc:
            raise ArtifactStaleConflictError(
                f"artifact registry is invalid: {exc}"
            ) from exc

        return handle.project_id, connection

    @staticmethod
    def _descendants(
        connection: sqlite3.Connection,
        trigger: ArtifactInputVersion,
        *,
        include_trigger_artifact: bool,
    ) -> tuple[ArtifactVersionRef, ...]:
        # Recursive SQL uses UNION (not UNION ALL) to guarantee bounded
        # convergence even if storage is externally corrupted into a cycle.
        # Registry verification is still the primary graph integrity authority.
        seed_kind = (
            "artifact"
            if trigger.entity_id.kind is IdKind.ARTIFACT
            else "requirement"
        )

        rows = connection.execute(
            """
            WITH RECURSIVE affected(artifact_id, version) AS (
                SELECT e.child_artifact_id, e.child_version
                FROM artifact_dependency_edges e
                WHERE e.dependency_kind=?
                  AND e.dependency_id=?
                  AND e.dependency_version=?

                UNION

                SELECT e.child_artifact_id, e.child_version
                FROM artifact_dependency_edges e
                JOIN affected a
                  ON e.dependency_kind='artifact'
                 AND e.dependency_id=a.artifact_id
                 AND e.dependency_version=a.version
            )
            SELECT artifact_id, version
            FROM affected
            ORDER BY artifact_id, version
            LIMIT ?
            """,
            (
                seed_kind,
                trigger.entity_id.value,
                trigger.version,
                MAX_STALE_PROPAGATION_ARTIFACTS + 1,
            ),
        ).fetchall()

        refs = [
            ArtifactVersionRef(
                artifact_id=artifact_id,
                version=version,
            )
            for artifact_id, version in rows
        ]

        if include_trigger_artifact:
            if trigger.entity_id.kind is not IdKind.ARTIFACT:
                raise ArtifactStaleConflictError(
                    "include_trigger_artifact requires artifact trigger"
                )
            root = ArtifactVersionRef(
                artifact_id=trigger.entity_id,
                version=trigger.version,
            )
            refs.insert(0, root)

        # Deduplicate while preserving deterministic order. The explicit root
        # may also be returned by recursive descendants only in corrupted data.
        unique: dict[tuple[str, int], ArtifactVersionRef] = {}
        for ref in refs:
            unique[(ref.artifact_id.value, ref.version)] = ref

        result = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.artifact_id.value,
                    item.version,
                ),
            )
        )

        if len(result) > MAX_STALE_PROPAGATION_ARTIFACTS:
            raise ArtifactStaleConflictError(
                "STALE propagation exceeds MAX_STALE_PROPAGATION_ARTIFACTS"
            )
        return result

    @staticmethod
    def _require_artifact_exists(
        connection: sqlite3.Connection,
        project_id: CanonicalId,
        ref: ArtifactVersionRef,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM artifact_versions
            WHERE artifact_id=? AND version=? AND project_id=?
            """,
            (
                ref.artifact_id.value,
                ref.version,
                project_id.value,
            ),
        ).fetchone()
        if row is None:
            raise ArtifactStaleNotFoundError(
                f"artifact version does not exist: "
                f"{ref.artifact_id.value}@v{ref.version}"
            )

    def propagate_dependency_invalidation(
        self,
        project_id: CanonicalId | str,
        dependency: ArtifactInputVersion,
    ) -> ArtifactStalePropagationResult:
        """Mark all direct/transitive artifact dependents of one exact input STALE."""

        if not isinstance(dependency, ArtifactInputVersion):
            raise TypeError("dependency must be ArtifactInputVersion")
        return self._propagate(
            project_id,
            dependency,
            include_trigger_artifact=False,
        )

    def mark_artifact_stale(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> ArtifactStalePropagationResult:
        """Mark one artifact version and every transitive dependent STALE."""

        try:
            artifact = (
                artifact_id
                if isinstance(artifact_id, CanonicalId)
                else CanonicalId.parse(artifact_id)
            )
        except (CanonicalIdError, TypeError) as exc:
            raise ArtifactStaleConflictError(
                "artifact_id must be canonical"
            ) from exc
        if artifact.kind is not IdKind.ARTIFACT:
            raise ArtifactStaleConflictError(
                "artifact_id must identify an artifact"
            )

        trigger = ArtifactInputVersion(
            entity_id=artifact,
            version=version,
        )
        return self._propagate(
            project_id,
            trigger,
            include_trigger_artifact=True,
        )

    def _propagate(
        self,
        project_id: CanonicalId | str,
        trigger: ArtifactInputVersion,
        *,
        include_trigger_artifact: bool,
    ) -> ArtifactStalePropagationResult:
        project, connection = self._open_registry(project_id)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                if include_trigger_artifact:
                    self._require_artifact_exists(
                        connection,
                        project,
                        ArtifactVersionRef(
                            artifact_id=trigger.entity_id,
                            version=trigger.version,
                        ),
                    )

                affected = self._descendants(
                    connection,
                    trigger,
                    include_trigger_artifact=include_trigger_artifact,
                )

                changed: list[ArtifactVersionRef] = []
                historical: list[ArtifactVersionRef] = []

                for ref in affected:
                    row = connection.execute(
                        """
                        SELECT l.status
                        FROM artifact_versions v
                        JOIN artifact_lifecycle l
                          ON l.artifact_id=v.artifact_id
                         AND l.version=v.version
                        WHERE v.artifact_id=?
                          AND v.version=?
                          AND v.project_id=?
                        """,
                        (
                            ref.artifact_id.value,
                            ref.version,
                            project.value,
                        ),
                    ).fetchone()
                    if row is None:
                        raise ArtifactStaleConflictError(
                            "lineage points to missing artifact lifecycle"
                        )

                    try:
                        current = parse_artifact_state(row[0])
                    except ArtifactStateError as exc:
                        raise ArtifactStaleConflictError(
                            "artifact lifecycle contains invalid state"
                        ) from exc

                    if current in HISTORICAL_ARTIFACT_STATES:
                        historical.append(ref)
                        continue

                    try:
                        require_artifact_transition(
                            current,
                            ArtifactState.STALE,
                        )
                    except InvalidArtifactStateTransition as exc:
                        raise ArtifactStaleConflictError(str(exc)) from exc

                    connection.execute(
                        """
                        UPDATE artifact_lifecycle
                        SET status='STALE'
                        WHERE artifact_id=?
                          AND version=?
                          AND status=?
                        """,
                        (
                            ref.artifact_id.value,
                            ref.version,
                            current.value,
                        ),
                    )
                    if connection.execute(
                        "SELECT changes()"
                    ).fetchone()[0] != 1:
                        raise ArtifactStaleConflictError(
                            "artifact lifecycle changed during STALE propagation"
                        )
                    changed.append(ref)

                connection.commit()
                return ArtifactStalePropagationResult(
                    trigger=trigger,
                    affected=affected,
                    changed_to_stale=tuple(changed),
                    already_historical=tuple(historical),
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
