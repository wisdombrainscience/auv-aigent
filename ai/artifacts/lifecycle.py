from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from ai.artifacts.schema import ArtifactRecord
from ai.artifacts.supersession import (
    ArtifactSupersessionConflictError,
    _auto_supersede_lower_versions,
)
from ai.artifacts.versions import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    _canonical_validation_result_json,
    _overlay_lifecycle,
    _parse_record_json,
    _parse_validation_result_json,
    initialize_artifact_registry,
    verify_artifact_registry,
)
from ai.contracts.artifact_states import (
    ArtifactState,
    ArtifactStateError,
    InvalidArtifactStateTransition,
    parse_artifact_state,
    require_artifact_transition,
)
from ai.contracts.ids import (
    CanonicalId,
    CanonicalIdError,
    IdKind,
)
from ai.contracts.validation_result import (
    ValidationResult,
    ValidationStatus,
)
from ai.project.store import (
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


ARTIFACT_LIFECYCLE_CONTRACT_VERSION = "1.2"


class ArtifactLifecycleError(RuntimeError):
    """Base error for persisted A4.5 artifact lifecycle transitions."""


class ArtifactLifecycleConflictError(ArtifactLifecycleError):
    """Raised when lifecycle state or transition evidence is inconsistent."""


class ArtifactLifecycleNotFoundError(ArtifactLifecycleError):
    """Raised when an artifact version does not exist."""


@dataclass(frozen=True)
class ArtifactTransitionResult:
    artifact: ArtifactRecord
    previous_state: ArtifactState
    current_state: ArtifactState

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRecord):
            raise TypeError("artifact must be ArtifactRecord")
        if not isinstance(self.previous_state, ArtifactState):
            raise TypeError("previous_state must be ArtifactState")
        if not isinstance(self.current_state, ArtifactState):
            raise TypeError("current_state must be ArtifactState")


def _parse_id(
    raw: CanonicalId | str,
    *,
    kind: IdKind,
    field: str,
) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactLifecycleConflictError(
            f"{field} must be a canonical {kind.value} ID"
        ) from exc
    if value.kind is not kind:
        raise ArtifactLifecycleConflictError(
            f"{field} must be a canonical {kind.value} ID"
        )
    return value


def _validation_for_transition(
    *,
    current_state: ArtifactState,
    target_state: ArtifactState,
    existing: ValidationResult | None,
    supplied: ValidationResult | None,
) -> ValidationResult | None:
    if supplied is not None and not isinstance(supplied, ValidationResult):
        raise ArtifactLifecycleConflictError(
            "validation_result must be ValidationResult or null"
        )

    if target_state is ArtifactState.VALIDATED:
        if supplied is None:
            raise ArtifactLifecycleConflictError(
                "VALIDATED transition requires deterministic ValidationResult"
            )
        if supplied.overall is not ValidationStatus.PASS:
            raise ArtifactLifecycleConflictError(
                "VALIDATED transition requires ValidationResult overall=PASS"
            )
        return supplied

    if target_state is ArtifactState.APPROVED:
        if supplied is not None:
            raise ArtifactLifecycleConflictError(
                "APPROVED transition cannot replace validation_result"
            )
        if existing is None or existing.overall is not ValidationStatus.PASS:
            raise ArtifactLifecycleConflictError(
                "APPROVED transition requires existing PASS validation_result"
            )
        return existing

    if target_state is ArtifactState.REJECTED:
        if supplied is None:
            return existing
        if supplied.overall is not ValidationStatus.FAIL:
            raise ArtifactLifecycleConflictError(
                "REJECTED transition accepts supplied validation_result only "
                "when overall=FAIL"
            )
        if existing is not None:
            raise ArtifactLifecycleConflictError(
                "REJECTED transition cannot replace existing validation_result"
            )
        return supplied

    if supplied is not None:
        raise ArtifactLifecycleConflictError(
            f"{target_state.value} transition does not accept validation_result"
        )

    # REVIEWED keeps null. STALE/SUPERSEDED preserve whatever deterministic
    # result the version already had.
    if target_state is ArtifactState.REVIEWED:
        if existing is not None:
            raise ArtifactLifecycleConflictError(
                "REVIEWED transition cannot retain prior validation_result"
            )
        return None

    return existing


class ArtifactLifecycleStore:
    """Execute one legal persisted state transition for an artifact version.

    A4.6 adds deterministic STALE propagation. A4.7 makes approval of a
    newer version automatically SUPERSEDE lower active versions of the same
    logical artifact, while keeping semantic downstream invalidation explicit.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_registry(
        self,
        project_id: CanonicalId | str,
    ) -> tuple[CanonicalId, sqlite3.Connection]:
        project = _parse_id(
            project_id,
            kind=IdKind.PROJECT,
            field="project_id",
        )
        try:
            handle = self._project_store.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ArtifactLifecycleConflictError(
                f"project could not be opened: {exc}"
            ) from exc

        path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
        if path.is_symlink():
            raise ArtifactLifecycleConflictError(
                "artifact registry must not be a symbolic link"
            )
        if not path.exists():
            raise ArtifactLifecycleNotFoundError(
                "artifact registry does not exist"
            )

        try:
            initialize_artifact_registry(path)
            connection = sqlite3.connect(path)
            verify_artifact_registry(connection)
        except ArtifactVersionConflictError as exc:
            raise ArtifactLifecycleConflictError(
                f"artifact registry is invalid: {exc}"
            ) from exc

        return handle.project_id, connection

    def transition(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
        target_state: ArtifactState | str,
        *,
        validation_result: ValidationResult | None = None,
    ) -> ArtifactTransitionResult:
        artifact = _parse_id(
            artifact_id,
            kind=IdKind.ARTIFACT,
            field="artifact_id",
        )
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or not 1 <= version <= 999_999
        ):
            raise ArtifactLifecycleNotFoundError(
                "artifact version is outside the supported range"
            )

        try:
            target = parse_artifact_state(target_state)
        except ArtifactStateError as exc:
            raise ArtifactLifecycleConflictError(
                f"target_state is invalid: {exc}"
            ) from exc

        project, connection = self._open_registry(project_id)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT v.project_id,
                           v.record_json,
                           l.status,
                           l.validation_result_json
                    FROM artifact_versions v
                    JOIN artifact_lifecycle l
                      ON l.artifact_id=v.artifact_id
                     AND l.version=v.version
                    WHERE v.artifact_id=? AND v.version=?
                    """,
                    (artifact.value, version),
                ).fetchone()
                if row is None:
                    raise ArtifactLifecycleNotFoundError(
                        f"artifact version does not exist: "
                        f"{artifact.value}@v{version}"
                    )
                if row[0] != project.value:
                    raise ArtifactLifecycleConflictError(
                        "artifact belongs to a different project"
                    )

                base = _parse_record_json(row[1])
                try:
                    current = parse_artifact_state(row[2])
                    target = require_artifact_transition(
                        current,
                        target,
                    )
                except (
                    ArtifactStateError,
                    InvalidArtifactStateTransition,
                ) as exc:
                    raise ArtifactLifecycleConflictError(str(exc)) from exc

                existing_validation = _parse_validation_result_json(
                    row[3]
                )
                if target is ArtifactState.APPROVED:
                    stale_parent = connection.execute(
                        """
                        SELECT e.dependency_id, e.dependency_version, parent.status
                        FROM artifact_dependency_edges e
                        JOIN artifact_lifecycle parent
                          ON parent.artifact_id=e.dependency_id
                         AND parent.version=e.dependency_version
                        WHERE e.child_artifact_id=?
                          AND e.child_version=?
                          AND e.dependency_kind='artifact'
                          AND parent.status IN ('STALE', 'SUPERSEDED')
                          AND NOT (
                              e.dependency_id=?
                              AND e.dependency_version<?
                          )
                        ORDER BY e.ordinal
                        LIMIT 1
                        """,
                        (
                            artifact.value,
                            version,
                            artifact.value,
                            version,
                        ),
                    ).fetchone()
                    if stale_parent is not None:
                        if stale_parent[2] == "STALE":
                            raise ArtifactLifecycleConflictError(
                                "APPROVED transition is forbidden while a required "
                                "artifact dependency is STALE: "
                                f"{stale_parent[0]}@v{stale_parent[1]}"
                            )
                        raise ArtifactLifecycleConflictError(
                            "APPROVED transition is forbidden while a required "
                            "artifact dependency is SUPERSEDED: "
                            f"{stale_parent[0]}@v{stale_parent[1]}"
                        )

                next_validation = _validation_for_transition(
                    current_state=current,
                    target_state=target,
                    existing=existing_validation,
                    supplied=validation_result,
                )
                validation_json = _canonical_validation_result_json(
                    next_validation
                )

                connection.execute(
                    """
                    UPDATE artifact_lifecycle
                    SET status=?, validation_result_json=?
                    WHERE artifact_id=? AND version=? AND status=?
                    """,
                    (
                        target.value,
                        validation_json,
                        artifact.value,
                        version,
                        current.value,
                    ),
                )
                if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0] != 1:
                    raise ArtifactLifecycleConflictError(
                        "artifact lifecycle changed during transition"
                    )

                if target is ArtifactState.APPROVED:
                    try:
                        _auto_supersede_lower_versions(
                            connection,
                            project_id=project,
                            artifact_id=artifact,
                            replacement_version=version,
                        )
                    except ArtifactSupersessionConflictError as exc:
                        raise ArtifactLifecycleConflictError(str(exc)) from exc

                result_record = _overlay_lifecycle(
                    base,
                    target,
                    next_validation,
                )
                connection.commit()
                return ArtifactTransitionResult(
                    artifact=result_record,
                    previous_state=current,
                    current_state=target,
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
