from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from ai.artifacts.hashing import (
    ArtifactContentStore,
    ArtifactContentStoreConflictError,
    ArtifactHashError,
    compute_artifact_sha256,
)
from ai.artifacts.schema import (
    MAX_ARTIFACT_VERSION,
    ArtifactInputVersion,
    ArtifactRecord,
    ArtifactSchemaError,
)
from ai.contracts.artifact_states import (
    ArtifactState,
    ArtifactStateError,
    parse_artifact_state,
)
from ai.contracts.validation_result import (
    ValidationResult,
    ValidationResultError,
    ValidationStatus,
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
from ai.project.schema import (
    ProjectSchemaError,
    verify_project_schema,
)
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


ARTIFACT_VERSION_STORE_CONTRACT_VERSION = "1.4"
ARTIFACT_REGISTRY_SCHEMA_VERSION = 4
ARTIFACT_DEPENDENCY_EDGE_CONTRACT_VERSION = "1.0"
ARTIFACT_REGISTRY_DB_FILENAME = "artifact_registry.sqlite3"

MAX_ARTIFACT_RECORD_JSON_BYTES = 512 * 1024

_A4_2_TABLES = (
    "artifact_heads",
    "artifact_registry_meta",
    "artifact_versions",
)

_A4_3_TABLES = (
    "artifact_dependency_edges",
    "artifact_heads",
    "artifact_registry_meta",
    "artifact_versions",
)

_A4_5_TABLES = (
    "artifact_dependency_edges",
    "artifact_heads",
    "artifact_lifecycle",
    "artifact_registry_meta",
    "artifact_versions",
)

_EXPECTED_TABLES = (
    "artifact_dependency_edges",
    "artifact_heads",
    "artifact_lifecycle",
    "artifact_registry_meta",
    "artifact_supersession",
    "artifact_versions",
)

_REGISTRY_DDL = (
    """
    CREATE TABLE artifact_registry_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version = 4)
    )
    """.strip(),
    """
    CREATE TABLE artifact_versions (
        artifact_id TEXT NOT NULL,
        version INTEGER NOT NULL
            CHECK (version >= 1 AND version <= 999999),
        project_id TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY (artifact_id, version),
        CHECK (
            artifact_id GLOB
            'ART-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (artifact_id <> 'ART-000000'),
        CHECK (
            project_id GLOB
            'UAV-PROJ-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (project_id <> 'UAV-PROJ-000000')
    )
    """.strip(),
    """
    CREATE TABLE artifact_heads (
        artifact_id TEXT PRIMARY KEY NOT NULL,
        latest_version INTEGER NOT NULL
            CHECK (latest_version >= 1 AND latest_version <= 999999),
        FOREIGN KEY (artifact_id, latest_version)
            REFERENCES artifact_versions(artifact_id, version)
            DEFERRABLE INITIALLY DEFERRED
    )
    """.strip(),
    """
    CREATE TABLE artifact_dependency_edges (
        child_artifact_id TEXT NOT NULL,
        child_version INTEGER NOT NULL
            CHECK (child_version >= 1 AND child_version <= 999999),
        ordinal INTEGER NOT NULL
            CHECK (ordinal >= 0 AND ordinal < 64),
        dependency_kind TEXT NOT NULL
            CHECK (dependency_kind IN ('requirement', 'artifact')),
        dependency_id TEXT NOT NULL,
        dependency_version INTEGER NOT NULL
            CHECK (dependency_version >= 1 AND dependency_version <= 999999),
        PRIMARY KEY (
            child_artifact_id,
            child_version,
            ordinal
        ),
        UNIQUE (
            child_artifact_id,
            child_version,
            dependency_kind,
            dependency_id,
            dependency_version
        ),
        FOREIGN KEY (child_artifact_id, child_version)
            REFERENCES artifact_versions(artifact_id, version)
            ON DELETE RESTRICT
    )
    """.strip(),
    """
    CREATE TABLE artifact_lifecycle (
        artifact_id TEXT NOT NULL,
        version INTEGER NOT NULL
            CHECK (version >= 1 AND version <= 999999),
        status TEXT NOT NULL
            CHECK (
                status IN (
                    'DRAFT',
                    'REVIEWED',
                    'VALIDATED',
                    'APPROVED',
                    'REJECTED',
                    'STALE',
                    'SUPERSEDED'
                )
            ),
        validation_result_json TEXT,
        PRIMARY KEY (artifact_id, version),
        FOREIGN KEY (artifact_id, version)
            REFERENCES artifact_versions(artifact_id, version)
            ON DELETE RESTRICT
    )
    """.strip(),
    """
    CREATE TABLE artifact_supersession (
        artifact_id TEXT NOT NULL,
        superseded_version INTEGER NOT NULL
            CHECK (superseded_version >= 1 AND superseded_version <= 999999),
        replacement_version INTEGER NOT NULL
            CHECK (replacement_version >= 1 AND replacement_version <= 999999),
        PRIMARY KEY (artifact_id, superseded_version),
        CHECK (replacement_version > superseded_version),
        FOREIGN KEY (artifact_id, superseded_version)
            REFERENCES artifact_versions(artifact_id, version)
            ON DELETE RESTRICT,
        FOREIGN KEY (artifact_id, replacement_version)
            REFERENCES artifact_versions(artifact_id, version)
            ON DELETE RESTRICT
    )
    """.strip(),
)


class ArtifactVersionStoreError(RuntimeError):
    """Base error for A4.2 artifact version persistence."""


class ArtifactVersionConflictError(ArtifactVersionStoreError):
    """Raised when artifact version history is corrupt or conflicting."""


class ArtifactVersionNotFoundError(ArtifactVersionStoreError):
    """Raised when an artifact or artifact version is absent."""



@dataclass(frozen=True)
class ArtifactVersionInput:
    """Input used to create one new immutable artifact content version.

    A4.4 owns the SHA-256 field. `content` is transient input data and is not
    embedded in ArtifactRecord JSON; it is persisted in the content-addressed
    filesystem store and the record stores only the computed SHA-256.
    """

    type: str
    producer_task: CanonicalId | str
    input_dependency_versions: tuple[ArtifactInputVersion, ...]
    content: bytes | bytearray | memoryview
    created_timestamp: str

    def __post_init__(self) -> None:
        dependencies = self.input_dependency_versions
        if not isinstance(dependencies, tuple):
            try:
                dependencies = tuple(dependencies)
            except TypeError as exc:
                raise ArtifactVersionConflictError(
                    "input_dependency_versions must be iterable"
                ) from exc
            object.__setattr__(
                self,
                "input_dependency_versions",
                dependencies,
            )

        if isinstance(self.content, bytes):
            content = self.content
        elif isinstance(self.content, (bytearray, memoryview)):
            content = bytes(self.content)
            object.__setattr__(self, "content", content)
        else:
            raise ArtifactVersionConflictError(
                "artifact content must be bytes-like"
            )

        try:
            digest = compute_artifact_sha256(content)
            probe = ArtifactRecord(
                artifact_id="ART-000001",
                project_id="UAV-PROJ-000001",
                version=1,
                type=self.type,
                producer_task=self.producer_task,
                input_dependency_versions=dependencies,
                hash=digest,
                status=ArtifactState.DRAFT,
                validation_result=None,
                created_timestamp=self.created_timestamp,
            )
        except (ArtifactSchemaError, ArtifactHashError) as exc:
            raise ArtifactVersionConflictError(
                f"artifact version input is invalid: {exc}"
            ) from exc

        object.__setattr__(
            self,
            "producer_task",
            probe.producer_task,
        )
        object.__setattr__(
            self,
            "input_dependency_versions",
            probe.input_dependency_versions,
        )
        object.__setattr__(self, "type", probe.type)
        object.__setattr__(
            self,
            "created_timestamp",
            probe.created_timestamp,
        )

    @property
    def computed_hash(self) -> str:
        return compute_artifact_sha256(self.content)


def _parse_id(

    raw: CanonicalId | str,
    *,
    kind: IdKind,
    field: str,
) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactVersionConflictError(
            f"{field} must be a canonical {kind.value} ID"
        ) from exc
    if value.kind is not kind:
        raise ArtifactVersionConflictError(
            f"{field} must be a canonical {kind.value} ID"
        )
    return value


def _registry_path(handle: ProjectHandle) -> Path:
    return handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME


def _reject_registry_path(path: Path) -> None:
    if path.is_symlink():
        raise ArtifactVersionConflictError(
            "artifact registry database must not be a symbolic link"
        )
    if path.exists() and not path.is_file():
        raise ArtifactVersionConflictError(
            "artifact registry database path is not a regular file"
        )


def _user_tables(
    connection: sqlite3.Connection,
    *,
    schema: str = "main",
) -> tuple[str, ...]:
    rows = connection.execute(
        f"""
        SELECT name
        FROM {schema}.sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return tuple(row[0] for row in rows)



def _dependency_kind(
    dependency: ArtifactInputVersion,
) -> str:
    if dependency.entity_id.kind is IdKind.REQUIREMENT:
        return "requirement"
    if dependency.entity_id.kind is IdKind.ARTIFACT:
        return "artifact"
    raise ArtifactVersionConflictError(
        "dependency kind is outside A4.3 contract"
    )


def _insert_dependency_edges(
    connection: sqlite3.Connection,
    *,
    child_artifact_id: CanonicalId,
    child_version: int,
    dependencies: tuple[ArtifactInputVersion, ...],
    schema_prefix: str = "",
) -> None:
    prefix = f"{schema_prefix}." if schema_prefix else ""
    for ordinal, dependency in enumerate(dependencies):
        connection.execute(
            f"""
            INSERT INTO {prefix}artifact_dependency_edges(
                child_artifact_id,
                child_version,
                ordinal,
                dependency_kind,
                dependency_id,
                dependency_version
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                child_artifact_id.value,
                child_version,
                ordinal,
                _dependency_kind(dependency),
                dependency.entity_id.value,
                dependency.version,
            ),
        )


def _backfill_dependency_edges(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        """
        SELECT artifact_id, version, record_json
        FROM artifact_versions
        ORDER BY rowid
        """
    ).fetchall()

    for artifact_id, version, record_json in rows:
        record = _parse_record_json(record_json)
        if (
            record.artifact_id.value != artifact_id
            or record.version != version
        ):
            raise ArtifactVersionConflictError(
                "A4.2 record identity mismatch during A4.3 migration"
            )

        # A4.2 allowed unresolved declarations. A4.3 migration refuses any
        # artifact edge that was a forward/self reference by insertion order.
        # This makes the persisted artifact-version graph acyclic without
        # unbounded traversal.
        child_rowid = connection.execute(
            """
            SELECT rowid
            FROM artifact_versions
            WHERE artifact_id=? AND version=?
            """,
            (artifact_id, version),
        ).fetchone()[0]

        for dependency in record.input_dependency_versions:
            if dependency.entity_id.kind is IdKind.ARTIFACT:
                target = connection.execute(
                    """
                    SELECT rowid
                    FROM artifact_versions
                    WHERE artifact_id=? AND version=?
                    """,
                    (
                        dependency.entity_id.value,
                        dependency.version,
                    ),
                ).fetchone()
                if target is None or target[0] >= child_rowid:
                    raise ArtifactVersionConflictError(
                        "A4.2 artifact dependency cannot be migrated: "
                        "target must already exist before child"
                    )

        _insert_dependency_edges(
            connection,
            child_artifact_id=record.artifact_id,
            child_version=record.version,
            dependencies=record.input_dependency_versions,
        )


def _validate_requirement_dependencies(
    project_store: ProjectStore,
    *,
    project_id: CanonicalId,
    dependencies: tuple[ArtifactInputVersion, ...],
) -> None:
    requirement_store = RequirementVersionStore(project_store)

    for dependency in dependencies:
        if dependency.entity_id.kind is not IdKind.REQUIREMENT:
            continue
        try:
            requirement_version = requirement_store.open_version(
                project_id,
                dependency.version,
            )
        except (
            RequirementVersionNotFoundError,
            RequirementVersionConflictError,
        ) as exc:
            raise ArtifactVersionConflictError(
                "requirement dependency version does not exist or is invalid: "
                f"{dependency.entity_id.value}@v{dependency.version}"
            ) from exc

        ids = {
            item.requirement_id.value
            for item in requirement_version.requirement_set.requirements
        }
        if dependency.entity_id.value not in ids:
            raise ArtifactVersionConflictError(
                "requirement dependency is not present in that requirement "
                f"version: {dependency.entity_id.value}@v{dependency.version}"
            )


def _validate_artifact_dependencies(
    connection: sqlite3.Connection,
    *,
    project_id: CanonicalId,
    dependencies: tuple[ArtifactInputVersion, ...],
    schema_prefix: str = "",
) -> None:
    prefix = f"{schema_prefix}." if schema_prefix else ""

    for dependency in dependencies:
        if dependency.entity_id.kind is not IdKind.ARTIFACT:
            continue

        row = connection.execute(
            f"""
            SELECT rowid, project_id, record_json
            FROM {prefix}artifact_versions
            WHERE artifact_id=? AND version=?
            """,
            (
                dependency.entity_id.value,
                dependency.version,
            ),
        ).fetchone()
        if row is None:
            raise ArtifactVersionConflictError(
                "artifact dependency version does not exist in this project: "
                f"{dependency.entity_id.value}@v{dependency.version}"
            )

        record = _parse_record_json(row[2])
        if record.project_id != project_id:
            raise ArtifactVersionConflictError(
                "artifact dependency belongs to a different project"
            )
        if (
            record.artifact_id != dependency.entity_id
            or record.version != dependency.version
        ):
            raise ArtifactVersionConflictError(
                "artifact dependency registry identity mismatch"
            )


def _verify_edge_record_consistency(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        """
        SELECT artifact_id, version, record_json
        FROM artifact_versions
        ORDER BY rowid
        """
    ).fetchall()

    for artifact_id, version, record_json in rows:
        record = _parse_record_json(record_json)
        expected = tuple(
            (
                ordinal,
                _dependency_kind(dependency),
                dependency.entity_id.value,
                dependency.version,
            )
            for ordinal, dependency
            in enumerate(record.input_dependency_versions)
        )
        actual = tuple(
            connection.execute(
                """
                SELECT ordinal,
                       dependency_kind,
                       dependency_id,
                       dependency_version
                FROM artifact_dependency_edges
                WHERE child_artifact_id=? AND child_version=?
                ORDER BY ordinal
                """,
                (artifact_id, version),
            ).fetchall()
        )
        if actual != expected:
            raise ArtifactVersionConflictError(
                "artifact dependency edges do not match immutable record"
            )

    invalid_artifact_edges = connection.execute(
        """
        SELECT COUNT(*)
        FROM artifact_dependency_edges e
        JOIN artifact_versions child
          ON child.artifact_id=e.child_artifact_id
         AND child.version=e.child_version
        LEFT JOIN artifact_versions target
          ON target.artifact_id=e.dependency_id
         AND target.version=e.dependency_version
        WHERE e.dependency_kind='artifact'
          AND (
              target.artifact_id IS NULL
              OR target.rowid >= child.rowid
          )
        """
    ).fetchone()[0]
    if invalid_artifact_edges:
        raise ArtifactVersionConflictError(
            "artifact dependency graph contains missing/forward/cyclic edge"
        )



def _canonical_validation_result_json(
    validation_result: ValidationResult | None,
) -> str | None:
    if validation_result is None:
        return None
    if not isinstance(validation_result, ValidationResult):
        raise ArtifactVersionConflictError(
            "validation_result must be ValidationResult or null"
        )
    return (
        json.dumps(
            validation_result.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _parse_validation_result_json(
    raw: str | None,
) -> ValidationResult | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ArtifactVersionConflictError(
            "validation_result_json must be text or null"
        )
    if len(raw.encode("utf-8")) > MAX_ARTIFACT_RECORD_JSON_BYTES:
        raise ArtifactVersionConflictError(
            "validation_result_json exceeds bounded size"
        )

    def reject_constant(value: str) -> None:
        raise ArtifactVersionConflictError(
            f"validation result contains non-finite JSON constant: {value}"
        )

    def object_without_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactVersionConflictError(
                    f"validation result contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except ArtifactVersionConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise ArtifactVersionConflictError(
            "validation_result_json is invalid JSON"
        ) from exc

    if not isinstance(payload, Mapping):
        raise ArtifactVersionConflictError(
            "validation_result_json top level must be an object"
        )
    try:
        return ValidationResult.from_dict(payload)
    except ValidationResultError as exc:
        raise ArtifactVersionConflictError(
            f"validation_result_json violates C1.7: {exc}"
        ) from exc


def _validate_lifecycle_pair(
    state: ArtifactState,
    validation_result: ValidationResult | None,
) -> None:
    if state in {
        ArtifactState.DRAFT,
        ArtifactState.REVIEWED,
    }:
        if validation_result is not None:
            raise ArtifactVersionConflictError(
                f"{state.value} artifact must not carry validation_result"
            )
        return

    if state in {
        ArtifactState.VALIDATED,
        ArtifactState.APPROVED,
    }:
        if (
            validation_result is None
            or validation_result.overall is not ValidationStatus.PASS
        ):
            raise ArtifactVersionConflictError(
                f"{state.value} artifact requires PASS validation_result"
            )


def _overlay_lifecycle(
    base: ArtifactRecord,
    state: ArtifactState,
    validation_result: ValidationResult | None,
) -> ArtifactRecord:
    _validate_lifecycle_pair(state, validation_result)
    return ArtifactRecord(
        artifact_id=base.artifact_id,
        project_id=base.project_id,
        version=base.version,
        type=base.type,
        producer_task=base.producer_task,
        input_dependency_versions=base.input_dependency_versions,
        hash=base.hash,
        status=state,
        validation_result=validation_result,
        created_timestamp=base.created_timestamp,
    )


def _backfill_artifact_lifecycle(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        """
        SELECT artifact_id, version, record_json
        FROM artifact_versions
        ORDER BY rowid
        """
    ).fetchall()

    for artifact_id, version, record_json in rows:
        record = _parse_record_json(record_json)
        try:
            state = parse_artifact_state(record.status)
        except ArtifactStateError as exc:
            raise ArtifactVersionConflictError(
                "stored artifact state is invalid during A4.5 migration"
            ) from exc
        _validate_lifecycle_pair(
            state,
            record.validation_result,
        )
        connection.execute(
            """
            INSERT INTO artifact_lifecycle(
                artifact_id,
                version,
                status,
                validation_result_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                artifact_id,
                version,
                state.value,
                _canonical_validation_result_json(
                    record.validation_result
                ),
            ),
        )


def _verify_lifecycle_consistency(
    connection: sqlite3.Connection,
) -> None:
    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM artifact_versions),
            (SELECT COUNT(*) FROM artifact_lifecycle)
        """
    ).fetchone()
    if counts is None or counts[0] != counts[1]:
        raise ArtifactVersionConflictError(
            "artifact lifecycle row count does not match artifact versions"
        )

    missing = connection.execute(
        """
        SELECT COUNT(*)
        FROM artifact_versions v
        LEFT JOIN artifact_lifecycle l
          ON l.artifact_id=v.artifact_id
         AND l.version=v.version
        WHERE l.artifact_id IS NULL
        """
    ).fetchone()[0]
    if missing:
        raise ArtifactVersionConflictError(
            "artifact version is missing lifecycle row"
        )

    rows = connection.execute(
        """
        SELECT l.status,
               l.validation_result_json
        FROM artifact_lifecycle l
        ORDER BY l.artifact_id, l.version
        """
    ).fetchall()

    for raw_state, raw_validation in rows:
        try:
            state = parse_artifact_state(raw_state)
        except ArtifactStateError as exc:
            raise ArtifactVersionConflictError(
                "artifact lifecycle contains invalid state"
            ) from exc
        validation = _parse_validation_result_json(
            raw_validation
        )
        _validate_lifecycle_pair(state, validation)



def _verify_supersession_consistency(
    connection: sqlite3.Connection,
) -> None:
    bad_rows = connection.execute(
        """
        SELECT COUNT(*)
        FROM artifact_supersession s
        JOIN artifact_lifecycle old
          ON old.artifact_id=s.artifact_id
         AND old.version=s.superseded_version
        LEFT JOIN artifact_versions replacement
          ON replacement.artifact_id=s.artifact_id
         AND replacement.version=s.replacement_version
        WHERE old.status <> 'SUPERSEDED'
           OR replacement.artifact_id IS NULL
           OR s.replacement_version <= s.superseded_version
        """
    ).fetchone()[0]
    if bad_rows:
        raise ArtifactVersionConflictError(
            "artifact supersession metadata is inconsistent"
        )

    duplicate_current = connection.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT artifact_id
            FROM artifact_lifecycle
            WHERE status='APPROVED'
            GROUP BY artifact_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_current:
        raise ArtifactVersionConflictError(
            "logical artifact has multiple APPROVED versions"
        )


def initialize_artifact_registry(
    path: Path,
) -> None:
    path = Path(path)
    if not path.is_absolute():
        raise ArtifactVersionConflictError(
            "artifact registry path must be absolute"
        )
    _reject_registry_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        existing = _user_tables(connection)

        if not existing:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _REGISTRY_DDL:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO artifact_registry_meta(
                        singleton,
                        schema_version
                    ) VALUES (1, ?)
                    """,
                    (ARTIFACT_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(
                    f"PRAGMA user_version={ARTIFACT_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        elif existing == _A4_2_TABLES:
            row = connection.execute(
                """
                SELECT schema_version
                FROM artifact_registry_meta
                WHERE singleton=1
                """
            ).fetchone()
            user_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            if row != (1,) or user_version != 1:
                raise ArtifactVersionConflictError(
                    "A4.2 registry metadata is not a valid migration source"
                )

            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "ALTER TABLE artifact_registry_meta "
                    "RENAME TO artifact_registry_meta_v1"
                )
                connection.execute(_REGISTRY_DDL[0])
                connection.execute(
                    """
                    INSERT INTO artifact_registry_meta(
                        singleton,
                        schema_version
                    ) VALUES (1, ?)
                    """,
                    (ARTIFACT_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(_REGISTRY_DDL[3])
                _backfill_dependency_edges(connection)
                connection.execute(_REGISTRY_DDL[4])
                _backfill_artifact_lifecycle(connection)
                connection.execute(_REGISTRY_DDL[5])
                connection.execute(
                    "DROP TABLE artifact_registry_meta_v1"
                )
                connection.execute(
                    f"PRAGMA user_version={ARTIFACT_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        elif existing == _A4_3_TABLES:
            row = connection.execute(
                """
                SELECT schema_version
                FROM artifact_registry_meta
                WHERE singleton=1
                """
            ).fetchone()
            user_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            if row != (2,) or user_version != 2:
                raise ArtifactVersionConflictError(
                    "A4.3 registry metadata is not a valid migration source"
                )

            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "ALTER TABLE artifact_registry_meta "
                    "RENAME TO artifact_registry_meta_v2"
                )
                connection.execute(_REGISTRY_DDL[0])
                connection.execute(
                    """
                    INSERT INTO artifact_registry_meta(
                        singleton,
                        schema_version
                    ) VALUES (1, ?)
                    """,
                    (ARTIFACT_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(_REGISTRY_DDL[4])
                _backfill_artifact_lifecycle(connection)
                connection.execute(_REGISTRY_DDL[5])
                connection.execute(
                    "DROP TABLE artifact_registry_meta_v2"
                )
                connection.execute(
                    f"PRAGMA user_version={ARTIFACT_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        elif existing == _A4_5_TABLES:
            row = connection.execute(
                """
                SELECT schema_version
                FROM artifact_registry_meta
                WHERE singleton=1
                """
            ).fetchone()
            user_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            if row != (3,) or user_version != 3:
                raise ArtifactVersionConflictError(
                    "A4.5/A4.6 registry metadata is not a valid migration source"
                )

            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "ALTER TABLE artifact_registry_meta "
                    "RENAME TO artifact_registry_meta_v3"
                )
                connection.execute(_REGISTRY_DDL[0])
                connection.execute(
                    """
                    INSERT INTO artifact_registry_meta(
                        singleton,
                        schema_version
                    ) VALUES (1, ?)
                    """,
                    (ARTIFACT_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(_REGISTRY_DDL[5])
                connection.execute(
                    "DROP TABLE artifact_registry_meta_v3"
                )
                connection.execute(
                    f"PRAGMA user_version={ARTIFACT_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        verify_artifact_registry(connection)
    finally:
        connection.close()


def verify_artifact_registry(

    connection: sqlite3.Connection,
) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")

    connection.execute("PRAGMA foreign_keys=ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ArtifactVersionConflictError(
            "artifact registry foreign_keys could not be enabled"
        )

    tables = _user_tables(connection)
    if tables != _EXPECTED_TABLES:
        raise ArtifactVersionConflictError(
            "unexpected artifact registry tables: "
            f"expected={_EXPECTED_TABLES!r} actual={tables!r}"
        )

    row = connection.execute(
        """
        SELECT schema_version
        FROM artifact_registry_meta
        WHERE singleton=1
        """
    ).fetchone()
    if row != (ARTIFACT_REGISTRY_SCHEMA_VERSION,):
        raise ArtifactVersionConflictError(
            "artifact registry schema metadata is invalid"
        )

    user_version = connection.execute(
        "PRAGMA user_version"
    ).fetchone()[0]
    if user_version != ARTIFACT_REGISTRY_SCHEMA_VERSION:
        raise ArtifactVersionConflictError(
            "artifact registry user_version mismatch"
        )

    quick = connection.execute("PRAGMA quick_check").fetchone()
    if quick is None or quick[0] != "ok":
        raise ArtifactVersionConflictError(
            "artifact registry quick_check failed"
        )

    duplicate_heads = connection.execute(
        """
        SELECT COUNT(*)
        FROM artifact_heads h
        LEFT JOIN (
            SELECT artifact_id, MAX(version) AS max_version
            FROM artifact_versions
            GROUP BY artifact_id
        ) v
        ON v.artifact_id = h.artifact_id
        WHERE v.artifact_id IS NULL
           OR h.latest_version <> v.max_version
        """
    ).fetchone()[0]
    if duplicate_heads != 0:
        raise ArtifactVersionConflictError(
            "artifact head/version history mismatch"
        )

    missing_heads = connection.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT artifact_id
            FROM artifact_versions
            GROUP BY artifact_id
        ) v
        LEFT JOIN artifact_heads h
          ON h.artifact_id = v.artifact_id
        WHERE h.artifact_id IS NULL
        """
    ).fetchone()[0]
    if missing_heads != 0:
        raise ArtifactVersionConflictError(
            "artifact history is missing head rows"
        )

    _verify_edge_record_consistency(connection)
    _verify_lifecycle_consistency(connection)
    _verify_supersession_consistency(connection)


def _canonical_record_json(record: ArtifactRecord) -> str:
    raw = (
        json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    encoded = raw.encode("utf-8")
    if len(encoded) > MAX_ARTIFACT_RECORD_JSON_BYTES:
        raise ArtifactVersionConflictError(
            "artifact record exceeds MAX_ARTIFACT_RECORD_JSON_BYTES"
        )
    return raw


def _parse_record_json(
    raw: str,
) -> ArtifactRecord:
    if not isinstance(raw, str):
        raise ArtifactVersionConflictError(
            "artifact record_json is not text"
        )
    if len(raw.encode("utf-8")) > MAX_ARTIFACT_RECORD_JSON_BYTES:
        raise ArtifactVersionConflictError(
            "artifact record_json exceeds bounded size"
        )

    def reject_constant(value: str) -> None:
        raise ArtifactVersionConflictError(
            f"artifact record contains non-finite JSON constant: {value}"
        )

    def object_without_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactVersionConflictError(
                    f"artifact record contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except ArtifactVersionConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise ArtifactVersionConflictError(
            "artifact record_json is invalid JSON"
        ) from exc

    if not isinstance(payload, Mapping):
        raise ArtifactVersionConflictError(
            "artifact record_json top level must be an object"
        )
    try:
        return ArtifactRecord.from_dict(payload)
    except ArtifactSchemaError as exc:
        raise ArtifactVersionConflictError(
            f"stored artifact record violates A4.1: {exc}"
        ) from exc


def _attach_registry(
    connection: sqlite3.Connection,
    registry_path: Path,
) -> None:
    _reject_registry_path(registry_path)
    connection.execute(
        "ATTACH DATABASE ? AS artifact_registry",
        (str(registry_path),),
    )
    connection.execute(
        "PRAGMA artifact_registry.foreign_keys=ON"
    )


def _verify_attached_registry(
    connection: sqlite3.Connection,
) -> None:
    tables = _user_tables(
        connection,
        schema="artifact_registry",
    )
    if tables != _EXPECTED_TABLES:
        raise ArtifactVersionConflictError(
            "attached artifact registry tables do not match A4.5"
        )

    row = connection.execute(
        """
        SELECT schema_version
        FROM artifact_registry.artifact_registry_meta
        WHERE singleton=1
        """
    ).fetchone()
    if row != (ARTIFACT_REGISTRY_SCHEMA_VERSION,):
        raise ArtifactVersionConflictError(
            "attached artifact registry version mismatch"
        )


class ArtifactVersionStore:
    """Persistent artifact identity/version registry with A4.3 edges.

    New logical artifacts receive globally allocated canonical ART IDs from the
    existing Phase-3 canonical_id_sequences table.

    Artifact versions are append-only in A4.2. Version 1 is created together
    with the ART ID; later versions increment exactly by one and retain the
    same artifact type. Every new version enters as DRAFT with no validation
    result.

    A4.3 requires every declared dependency version to exist before the
    child artifact version is committed and persists the declaration as
    immutable ordered dependency edges. Existing-only artifact targets
    make forward/self cycles impossible for new writes.

    A4.4 computes and verifies SHA-256 over exact artifact payload bytes,
    persisted in a content-addressed filesystem store.

    A4.5 stores current lifecycle state separately from immutable artifact
    creation records. New versions still enter DRAFT.

    This store still deliberately does not automatically propagate STALE
    or SUPERSEDED; A4.6/A4.7 own those policies.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._content_store = ArtifactContentStore(project_store)

    @property
    def projects_root(self) -> Path:
        return self._project_store.projects_root

    def _open_handle(
        self,
        project_id: CanonicalId | str,
    ) -> ProjectHandle:
        project = _parse_id(
            project_id,
            kind=IdKind.PROJECT,
            field="project_id",
        )
        try:
            return self._project_store.open_project(project)
        except (
            ProjectNotFoundError,
            ProjectStoreConflictError,
        ) as exc:
            raise ArtifactVersionConflictError(
                f"project could not be opened: {exc}"
            ) from exc

    def _ensure_registry(
        self,
        handle: ProjectHandle,
    ) -> Path:
        path = _registry_path(handle)
        initialize_artifact_registry(path)
        return path

    def create_artifact(
        self,
        project_id: CanonicalId | str,
        value: ArtifactVersionInput,
    ) -> ArtifactRecord:
        if not isinstance(value, ArtifactVersionInput):
            raise TypeError("value must be ArtifactVersionInput")

        handle = self._open_handle(project_id)
        registry_path = self._ensure_registry(handle)

        connection = sqlite3.connect(handle.database_path)
        try:
            verify_project_schema(connection)
            _attach_registry(connection, registry_path)
            _verify_attached_registry(connection)

            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT last_sequence
                    FROM canonical_id_sequences
                    WHERE kind=?
                    """,
                    (IdKind.ARTIFACT.value,),
                ).fetchone()
                if (
                    row is None
                    or isinstance(row[0], bool)
                    or not isinstance(row[0], int)
                    or row[0] < 0
                ):
                    raise ArtifactVersionConflictError(
                        "artifact canonical ID sequence is invalid"
                    )

                next_sequence = row[0] + 1
                try:
                    artifact_id = CanonicalId.from_sequence(
                        IdKind.ARTIFACT,
                        next_sequence,
                    )
                except CanonicalIdError as exc:
                    raise ArtifactVersionConflictError(
                        "artifact canonical ID sequence is exhausted"
                    ) from exc

                record = ArtifactRecord(
                    artifact_id=artifact_id,
                    project_id=handle.project_id,
                    version=1,
                    type=value.type,
                    producer_task=value.producer_task,
                    input_dependency_versions=value.input_dependency_versions,
                    hash=value.computed_hash,
                    status=ArtifactState.DRAFT,
                    validation_result=None,
                    created_timestamp=value.created_timestamp,
                )
                record_json = _canonical_record_json(record)

                content_ref = self._content_store.put(
                    handle.project_id,
                    value.content,
                )
                if content_ref.sha256 != record.hash:
                    raise ArtifactVersionConflictError(
                        "computed content hash mismatch"
                    )

                _validate_requirement_dependencies(
                    self._project_store,
                    project_id=handle.project_id,
                    dependencies=record.input_dependency_versions,
                )
                _validate_artifact_dependencies(
                    connection,
                    project_id=handle.project_id,
                    dependencies=record.input_dependency_versions,
                    schema_prefix="artifact_registry",
                )

                connection.execute(
                    """
                    INSERT INTO artifact_registry.artifact_versions(
                        artifact_id,
                        version,
                        project_id,
                        artifact_type,
                        record_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.artifact_id.value,
                        record.version,
                        record.project_id.value,
                        record.type,
                        record_json,
                    ),
                )
                _insert_dependency_edges(
                    connection,
                    child_artifact_id=record.artifact_id,
                    child_version=record.version,
                    dependencies=record.input_dependency_versions,
                    schema_prefix="artifact_registry",
                )
                connection.execute(
                    """
                    INSERT INTO artifact_registry.artifact_lifecycle(
                        artifact_id,
                        version,
                        status,
                        validation_result_json
                    ) VALUES (?, ?, ?, NULL)
                    """,
                    (
                        record.artifact_id.value,
                        record.version,
                        ArtifactState.DRAFT.value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifact_registry.artifact_heads(
                        artifact_id,
                        latest_version
                    ) VALUES (?, ?)
                    """,
                    (
                        record.artifact_id.value,
                        record.version,
                    ),
                )
                connection.execute(
                    """
                    UPDATE canonical_id_sequences
                    SET last_sequence=?
                    WHERE kind=?
                    """,
                    (
                        next_sequence,
                        IdKind.ARTIFACT.value,
                    ),
                )
                if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0] != 1:
                    raise ArtifactVersionConflictError(
                        "artifact canonical ID sequence update failed"
                    )

                connection.commit()
                return record
            except Exception:
                connection.rollback()
                if 'content_ref' in locals():
                    try:
                        self._content_store.discard_created(
                            handle.project_id,
                            content_ref,
                        )
                    except ArtifactHashError:
                        pass
                raise
        except ProjectSchemaError as exc:
            raise ArtifactVersionConflictError(
                f"Project State schema is invalid: {exc}"
            ) from exc
        finally:
            connection.close()

    def append_version(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        value: ArtifactVersionInput,
    ) -> ArtifactRecord:
        if not isinstance(value, ArtifactVersionInput):
            raise TypeError("value must be ArtifactVersionInput")

        handle = self._open_handle(project_id)
        artifact = _parse_id(
            artifact_id,
            kind=IdKind.ARTIFACT,
            field="artifact_id",
        )
        registry_path = self._ensure_registry(handle)

        connection = sqlite3.connect(registry_path)
        try:
            verify_artifact_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = connection.execute(
                    """
                    SELECT latest_version
                    FROM artifact_heads
                    WHERE artifact_id=?
                    """,
                    (artifact.value,),
                ).fetchone()
                if head is None:
                    raise ArtifactVersionNotFoundError(
                        f"artifact does not exist: {artifact.value}"
                    )

                latest_version = head[0]
                if (
                    isinstance(latest_version, bool)
                    or not isinstance(latest_version, int)
                    or latest_version < 1
                ):
                    raise ArtifactVersionConflictError(
                        "artifact head version is invalid"
                    )
                if latest_version >= MAX_ARTIFACT_VERSION:
                    raise ArtifactVersionConflictError(
                        "artifact version sequence is exhausted"
                    )

                previous_row = connection.execute(
                    """
                    SELECT project_id, artifact_type, record_json
                    FROM artifact_versions
                    WHERE artifact_id=? AND version=?
                    """,
                    (
                        artifact.value,
                        latest_version,
                    ),
                ).fetchone()
                if previous_row is None:
                    raise ArtifactVersionConflictError(
                        "artifact head points to a missing version"
                    )

                previous = _parse_record_json(previous_row[2])
                if previous.project_id != handle.project_id:
                    raise ArtifactVersionConflictError(
                        "artifact belongs to a different project"
                    )
                if previous.artifact_id != artifact:
                    raise ArtifactVersionConflictError(
                        "artifact history identity mismatch"
                    )
                if previous.type != value.type:
                    raise ArtifactVersionConflictError(
                        "artifact type must remain stable across versions"
                    )

                next_version = latest_version + 1
                record = ArtifactRecord(
                    artifact_id=artifact,
                    project_id=handle.project_id,
                    version=next_version,
                    type=value.type,
                    producer_task=value.producer_task,
                    input_dependency_versions=value.input_dependency_versions,
                    hash=value.computed_hash,
                    status=ArtifactState.DRAFT,
                    validation_result=None,
                    created_timestamp=value.created_timestamp,
                )
                record_json = _canonical_record_json(record)

                content_ref = self._content_store.put(
                    handle.project_id,
                    value.content,
                )
                if content_ref.sha256 != record.hash:
                    raise ArtifactVersionConflictError(
                        "computed content hash mismatch"
                    )

                _validate_requirement_dependencies(
                    self._project_store,
                    project_id=handle.project_id,
                    dependencies=record.input_dependency_versions,
                )
                _validate_artifact_dependencies(
                    connection,
                    project_id=handle.project_id,
                    dependencies=record.input_dependency_versions,
                )

                connection.execute(
                    """
                    INSERT INTO artifact_versions(
                        artifact_id,
                        version,
                        project_id,
                        artifact_type,
                        record_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.artifact_id.value,
                        record.version,
                        record.project_id.value,
                        record.type,
                        record_json,
                    ),
                )
                _insert_dependency_edges(
                    connection,
                    child_artifact_id=record.artifact_id,
                    child_version=record.version,
                    dependencies=record.input_dependency_versions,
                )
                connection.execute(
                    """
                    INSERT INTO artifact_lifecycle(
                        artifact_id,
                        version,
                        status,
                        validation_result_json
                    ) VALUES (?, ?, ?, NULL)
                    """,
                    (
                        record.artifact_id.value,
                        record.version,
                        ArtifactState.DRAFT.value,
                    ),
                )
                connection.execute(
                    """
                    UPDATE artifact_heads
                    SET latest_version=?
                    WHERE artifact_id=? AND latest_version=?
                    """,
                    (
                        next_version,
                        artifact.value,
                        latest_version,
                    ),
                )
                if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0] != 1:
                    raise ArtifactVersionConflictError(
                        "artifact head changed during append"
                    )

                connection.commit()
                return record
            except Exception:
                connection.rollback()
                if 'content_ref' in locals():
                    try:
                        self._content_store.discard_created(
                            handle.project_id,
                            content_ref,
                        )
                    except ArtifactHashError:
                        pass
                raise
        finally:
            connection.close()

    def open_version(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> ArtifactRecord:
        handle = self._open_handle(project_id)
        artifact = _parse_id(
            artifact_id,
            kind=IdKind.ARTIFACT,
            field="artifact_id",
        )
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or not 1 <= version <= MAX_ARTIFACT_VERSION
        ):
            raise ArtifactVersionNotFoundError(
                "artifact version is outside the supported range"
            )

        path = _registry_path(handle)
        if not path.exists():
            raise ArtifactVersionNotFoundError(
                "artifact registry does not exist"
            )
        _reject_registry_path(path)

        connection = sqlite3.connect(path)
        try:
            verify_artifact_registry(connection)
            row = connection.execute(
                """
                SELECT v.project_id,
                       v.artifact_type,
                       v.record_json,
                       l.status,
                       l.validation_result_json
                FROM artifact_versions v
                JOIN artifact_lifecycle l
                  ON l.artifact_id=v.artifact_id
                 AND l.version=v.version
                WHERE v.artifact_id=? AND v.version=?
                """,
                (
                    artifact.value,
                    version,
                ),
            ).fetchone()
            if row is None:
                raise ArtifactVersionNotFoundError(
                    f"artifact version does not exist: "
                    f"{artifact.value}@v{version}"
                )

            record = _parse_record_json(row[2])
            if record.project_id != handle.project_id:
                raise ArtifactVersionConflictError(
                    "stored artifact project mismatch"
                )
            if record.artifact_id != artifact:
                raise ArtifactVersionConflictError(
                    "stored artifact identity mismatch"
                )
            if record.version != version:
                raise ArtifactVersionConflictError(
                    "stored artifact version mismatch"
                )
            if row[0] != record.project_id.value:
                raise ArtifactVersionConflictError(
                    "artifact registry project index mismatch"
                )
            if row[1] != record.type:
                raise ArtifactVersionConflictError(
                    "artifact registry type index mismatch"
                )
            try:
                lifecycle_state = parse_artifact_state(row[3])
            except ArtifactStateError as exc:
                raise ArtifactVersionConflictError(
                    "artifact lifecycle state is invalid"
                ) from exc
            validation_result = _parse_validation_result_json(
                row[4]
            )
            record = _overlay_lifecycle(
                record,
                lifecycle_state,
                validation_result,
            )
            try:
                self._content_store.verify(
                    handle.project_id,
                    record.hash,
                )
            except ArtifactHashError as exc:
                raise ArtifactVersionConflictError(
                    f"artifact content integrity failure: {exc}"
                ) from exc
            return record
        finally:
            connection.close()

    def open_latest(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
    ) -> ArtifactRecord:
        handle = self._open_handle(project_id)
        artifact = _parse_id(
            artifact_id,
            kind=IdKind.ARTIFACT,
            field="artifact_id",
        )
        path = _registry_path(handle)
        if not path.exists():
            raise ArtifactVersionNotFoundError(
                "artifact registry does not exist"
            )
        _reject_registry_path(path)

        connection = sqlite3.connect(path)
        try:
            verify_artifact_registry(connection)
            row = connection.execute(
                """
                SELECT latest_version
                FROM artifact_heads
                WHERE artifact_id=?
                """,
                (artifact.value,),
            ).fetchone()
            if row is None:
                raise ArtifactVersionNotFoundError(
                    f"artifact does not exist: {artifact.value}"
                )
            version = row[0]
        finally:
            connection.close()

        return self.open_version(
            handle.project_id,
            artifact,
            version,
        )

    def list_versions(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
    ) -> tuple[int, ...]:
        handle = self._open_handle(project_id)
        artifact = _parse_id(
            artifact_id,
            kind=IdKind.ARTIFACT,
            field="artifact_id",
        )
        path = _registry_path(handle)
        if not path.exists():
            raise ArtifactVersionNotFoundError(
                "artifact registry does not exist"
            )

        connection = sqlite3.connect(path)
        try:
            verify_artifact_registry(connection)
            versions = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT version
                    FROM artifact_versions
                    WHERE artifact_id=?
                    ORDER BY version
                    """,
                    (artifact.value,),
                ).fetchall()
            )
            if not versions:
                raise ArtifactVersionNotFoundError(
                    f"artifact does not exist: {artifact.value}"
                )
            return versions
        finally:
            connection.close()
