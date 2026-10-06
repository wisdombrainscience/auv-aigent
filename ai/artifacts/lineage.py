from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from ai.artifacts.schema import ArtifactInputVersion
from ai.artifacts.versions import (
    ARTIFACT_DEPENDENCY_EDGE_CONTRACT_VERSION,
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    initialize_artifact_registry,
    verify_artifact_registry,
)
from ai.contracts.ids import (
    CanonicalId,
    CanonicalIdError,
    IdKind,
)
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


ARTIFACT_LINEAGE_STORE_CONTRACT_VERSION = "1.0"
MAX_DIRECT_DEPENDENTS_RESULT = 4096


class ArtifactLineageError(RuntimeError):
    """Base error for A4.3 lineage queries."""


class ArtifactLineageConflictError(ArtifactLineageError):
    """Raised when persisted lineage is corrupt or inconsistent."""


class ArtifactLineageNotFoundError(ArtifactLineageError):
    """Raised when a child artifact version does not exist."""


@dataclass(frozen=True, order=True)
class ArtifactVersionRef:
    artifact_id: CanonicalId
    version: int

    def __post_init__(self) -> None:
        value = self.artifact_id
        if not isinstance(value, CanonicalId):
            try:
                value = CanonicalId.parse(value)
            except (CanonicalIdError, TypeError) as exc:
                raise ArtifactLineageConflictError(
                    "artifact_id must be canonical"
                ) from exc
            object.__setattr__(self, "artifact_id", value)

        if value.kind is not IdKind.ARTIFACT:
            raise ArtifactLineageConflictError(
                "artifact_id must identify an artifact"
            )
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or not 1 <= self.version <= 999_999
        ):
            raise ArtifactLineageConflictError(
                "artifact version is invalid"
            )


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactLineageConflictError(
            "project_id must be canonical"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise ArtifactLineageConflictError(
            "project_id must identify a project"
        )
    return value


def _parse_artifact_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactLineageConflictError(
            "artifact_id must be canonical"
        ) from exc
    if value.kind is not IdKind.ARTIFACT:
        raise ArtifactLineageConflictError(
            "artifact_id must identify an artifact"
        )
    return value


class ArtifactLineageStore:
    """Read and verify persisted A4.3 dependency edges.

    This layer is read-only. Version creation remains the sole writer so edge
    persistence cannot drift from immutable ArtifactRecord declarations.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open(self, project_id: CanonicalId | str):
        project = _parse_project_id(project_id)
        try:
            handle = self._project_store.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ArtifactLineageConflictError(
                f"project could not be opened: {exc}"
            ) from exc

        path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
        if path.is_symlink():
            raise ArtifactLineageConflictError(
                "artifact registry must not be a symbolic link"
            )
        if not path.exists():
            raise ArtifactLineageNotFoundError(
                "artifact registry does not exist"
            )
        initialize_artifact_registry(path)
        return handle, path

    def dependencies(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> tuple[ArtifactInputVersion, ...]:
        handle, path = self._open(project_id)
        artifact = _parse_artifact_id(artifact_id)
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or not 1 <= version <= 999_999
        ):
            raise ArtifactLineageNotFoundError(
                "artifact version is invalid"
            )

        with sqlite3.connect(path) as connection:
            verify_artifact_registry(connection)
            exists = connection.execute(
                """
                SELECT 1
                FROM artifact_versions
                WHERE artifact_id=? AND version=? AND project_id=?
                """,
                (artifact.value, version, handle.project_id.value),
            ).fetchone()
            if exists is None:
                raise ArtifactLineageNotFoundError(
                    f"artifact version does not exist: {artifact.value}@v{version}"
                )

            rows = connection.execute(
                """
                SELECT dependency_id, dependency_version
                FROM artifact_dependency_edges
                WHERE child_artifact_id=? AND child_version=?
                ORDER BY ordinal
                """,
                (artifact.value, version),
            ).fetchall()

        return tuple(
            ArtifactInputVersion(
                entity_id=dependency_id,
                version=dependency_version,
            )
            for dependency_id, dependency_version in rows
        )

    def direct_dependents(
        self,
        project_id: CanonicalId | str,
        dependency: ArtifactInputVersion,
    ) -> tuple[ArtifactVersionRef, ...]:
        if not isinstance(dependency, ArtifactInputVersion):
            raise TypeError("dependency must be ArtifactInputVersion")

        _handle, path = self._open(project_id)
        with sqlite3.connect(path) as connection:
            verify_artifact_registry(connection)
            rows = connection.execute(
                """
                SELECT child_artifact_id, child_version
                FROM artifact_dependency_edges
                WHERE dependency_id=? AND dependency_version=?
                ORDER BY child_artifact_id, child_version
                LIMIT ?
                """,
                (
                    dependency.entity_id.value,
                    dependency.version,
                    MAX_DIRECT_DEPENDENTS_RESULT + 1,
                ),
            ).fetchall()

        if len(rows) > MAX_DIRECT_DEPENDENTS_RESULT:
            raise ArtifactLineageConflictError(
                "direct dependents result exceeds bounded limit"
            )

        return tuple(
            ArtifactVersionRef(
                artifact_id=artifact_id,
                version=version,
            )
            for artifact_id, version in rows
        )

    def verify_project(
        self,
        project_id: CanonicalId | str,
    ) -> None:
        handle, path = self._open(project_id)
        requirement_store = RequirementVersionStore(self._project_store)

        with sqlite3.connect(path) as connection:
            verify_artifact_registry(connection)
            requirement_edges = connection.execute(
                """
                SELECT dependency_id, dependency_version
                FROM artifact_dependency_edges
                WHERE dependency_kind='requirement'
                ORDER BY dependency_id, dependency_version
                """
            ).fetchall()

        for requirement_id, version in requirement_edges:
            try:
                snapshot = requirement_store.open_version(
                    handle.project_id,
                    version,
                )
            except (
                RequirementVersionNotFoundError,
                RequirementVersionConflictError,
            ) as exc:
                raise ArtifactLineageConflictError(
                    "requirement lineage target does not exist"
                ) from exc

            ids = {
                item.requirement_id.value
                for item in snapshot.requirement_set.requirements
            }
            if requirement_id not in ids:
                raise ArtifactLineageConflictError(
                    "requirement lineage target is absent from referenced version"
                )
