from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import MappingProxyType
from typing import Any, Mapping

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.layout import (
    PROJECT_LAYOUT_CONTRACT_VERSION,
    ProjectLayout,
    ProjectLayoutError,
    default_projects_root,
)
from ai.project.schema import (
    PROJECT_SCHEMA_VERSION,
    ProjectSchemaError,
    initialize_project_schema,
    verify_project_schema,
)


PROJECT_STORE_CONTRACT_VERSION = "1.0"
PROJECT_METADATA_FORMAT_VERSION = 1
PROJECT_STATE_DB_FILENAME = "project_state.sqlite3"
MAX_PROJECT_METADATA_BYTES = 64 * 1024

_PROJECT_METADATA_KEYS = frozenset(
    {
        "format_version",
        "project_id",
        "project_schema_version",
        "project_layout_contract_version",
        "project_store_contract_version",
    }
)


class ProjectStoreError(RuntimeError):
    """Base error for S3.3 create/open project operations."""


class ProjectStoreConflictError(ProjectStoreError):
    """Raised when persistent project state is partial, conflicting, or corrupt."""


class ProjectNotFoundError(ProjectStoreError):
    """Raised when a requested project does not exist in authoritative state."""


@dataclass(frozen=True)
class ProjectHandle:
    """Immutable descriptor returned by create/open project operations."""

    project_id: CanonicalId
    layout: ProjectLayout
    database_path: Path
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.project_id.kind is not IdKind.PROJECT:
            raise ProjectStoreConflictError("ProjectHandle requires a project ID")
        if not isinstance(self.layout, ProjectLayout):
            raise TypeError("layout must be ProjectLayout")
        if self.layout.project_id != self.project_id:
            raise ProjectStoreConflictError("layout/project ID mismatch")
        database_path = Path(self.database_path)
        if not database_path.is_absolute():
            raise ProjectStoreConflictError("database_path must be absolute")
        object.__setattr__(self, "database_path", database_path)
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id.value,
            "project_dir": str(self.layout.project_dir),
            "project_json": str(self.layout.project_json),
            "database_path": str(self.database_path),
            "metadata": dict(self.metadata),
        }


def _reject_symlink(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ProjectStoreConflictError(
            f"{label} must not be a symbolic link: {path}"
        )


def _metadata_for(project_id: CanonicalId) -> dict[str, object]:
    return {
        "format_version": PROJECT_METADATA_FORMAT_VERSION,
        "project_id": project_id.value,
        "project_schema_version": PROJECT_SCHEMA_VERSION,
        "project_layout_contract_version": PROJECT_LAYOUT_CONTRACT_VERSION,
        "project_store_contract_version": PROJECT_STORE_CONTRACT_VERSION,
    }


def _reject_json_constant(raw: str) -> None:
    raise ProjectStoreConflictError(
        f"project metadata contains non-finite JSON constant: {raw}"
    )


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectStoreConflictError(
                f"project metadata contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _validate_metadata_value(value: object, *, path: str = "metadata") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProjectStoreConflictError(
                f"project metadata contains non-finite number at {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_metadata_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProjectStoreConflictError(
                    f"project metadata key at {path} must be text"
                )
            _validate_metadata_value(item, path=f"{path}.{key}")
        return
    raise ProjectStoreConflictError(
        f"project metadata contains unsupported value at {path}"
    )


def _parse_project_metadata(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_PROJECT_METADATA_BYTES:
        raise ProjectStoreConflictError("project metadata is too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectStoreConflictError(
            "project metadata is not valid UTF-8"
        ) from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except ProjectStoreConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise ProjectStoreConflictError(
            "project metadata is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ProjectStoreConflictError(
            "project metadata top level must be an object"
        )
    _validate_metadata_value(payload)
    keys = frozenset(payload.keys())
    if keys != _PROJECT_METADATA_KEYS:
        missing = sorted(_PROJECT_METADATA_KEYS - keys)
        extra = sorted(keys - _PROJECT_METADATA_KEYS)
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise ProjectStoreConflictError(
            "project metadata keys do not match S3.3 contract"
            + (" (" + "; ".join(detail) + ")" if detail else "")
        )
    return payload


def _validate_project_metadata(
    metadata: Mapping[str, object],
    *,
    project_id: CanonicalId,
) -> dict[str, object]:
    payload = dict(metadata)
    if payload.get("format_version") != PROJECT_METADATA_FORMAT_VERSION:
        raise ProjectStoreConflictError(
            "unsupported project metadata format version"
        )
    if payload.get("project_id") != project_id.value:
        raise ProjectStoreConflictError("project metadata ID mismatch")
    if payload.get("project_schema_version") != PROJECT_SCHEMA_VERSION:
        raise ProjectStoreConflictError("project metadata schema version mismatch")
    if (
        payload.get("project_layout_contract_version")
        != PROJECT_LAYOUT_CONTRACT_VERSION
    ):
        raise ProjectStoreConflictError("project metadata layout version mismatch")
    if (
        payload.get("project_store_contract_version")
        != PROJECT_STORE_CONTRACT_VERSION
    ):
        raise ProjectStoreConflictError("project metadata store version mismatch")
    return payload


def _read_project_metadata(
    path: Path,
    *,
    project_id: CanonicalId,
) -> dict[str, object]:
    if not path.exists():
        raise ProjectStoreConflictError("project.json is missing")
    _reject_symlink(path, label="project.json")
    if not path.is_file():
        raise ProjectStoreConflictError("project.json is not a regular file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PROJECT_METADATA_BYTES + 1)
    except OSError as exc:
        raise ProjectStoreConflictError("project.json could not be read") from exc
    return _validate_project_metadata(
        _parse_project_metadata(raw),
        project_id=project_id,
    )


def _write_project_metadata(path: Path, metadata: Mapping[str, object]) -> None:
    encoded = (
        json.dumps(
            dict(metadata),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_PROJECT_METADATA_BYTES:
        raise ProjectStoreConflictError("project metadata is too large")

    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(path.parent, label="project directory")
    if path.exists() or path.is_symlink():
        raise ProjectStoreConflictError("project.json already exists")

    fd, tmp_name = tempfile.mkstemp(
        prefix=".project.json.",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        tmp.unlink(missing_ok=True)


class ProjectStore:
    """Minimal S3.3 create/open boundary for local Project State.

    A single SQLite file lives beside the fixed projects/ directory. SQLite is
    authoritative for project existence and canonical project-ID allocation;
    the filesystem carries the fixed project layout and project.json metadata.

    This class does not expose a general transaction API. S3.9 remains the
    owner of the reusable Project State transaction wrapper.
    """

    def __init__(self, projects_root: Path | str | None = None) -> None:
        if projects_root is None:
            projects_root = default_projects_root()
        root = Path(projects_root).expanduser()
        if not root.is_absolute():
            raise ProjectStoreConflictError("projects_root must be absolute")
        self._projects_root = root
        self._database_path = root.parent / PROJECT_STATE_DB_FILENAME

    @property
    def projects_root(self) -> Path:
        return self._projects_root

    @property
    def database_path(self) -> Path:
        return self._database_path

    def _prepare_storage_parent(self) -> None:
        data_root = self._projects_root.parent
        _reject_symlink(data_root, label="Project State data root")
        data_root.mkdir(parents=True, exist_ok=True)
        _reject_symlink(data_root, label="Project State data root")
        _reject_symlink(self._database_path, label="Project State database")

    def _connect_existing(self) -> sqlite3.Connection:
        if not self._database_path.exists():
            raise ProjectNotFoundError("Project State database does not exist")
        _reject_symlink(self._database_path, label="Project State database")
        if not self._database_path.is_file():
            raise ProjectStoreConflictError(
                "Project State database path is not a regular file"
            )
        return sqlite3.connect(self._database_path)

    def create_project(self) -> ProjectHandle:
        """Allocate one canonical project ID and create its persistent layout."""

        self._prepare_storage_parent()
        self._projects_root.mkdir(parents=True, exist_ok=True)
        _reject_symlink(self._projects_root, label="projects_root")

        connection = sqlite3.connect(self._database_path)
        layout: ProjectLayout | None = None
        project_dir_existed = False
        try:
            initialize_project_schema(connection)
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                "SELECT last_sequence FROM canonical_id_sequences WHERE kind = ?",
                (IdKind.PROJECT.value,),
            ).fetchone()
            if (
                row is None
                or isinstance(row[0], bool)
                or not isinstance(row[0], int)
                or row[0] < 0
            ):
                raise ProjectStoreConflictError(
                    "project canonical ID sequence row is invalid"
                )

            try:
                project_id = CanonicalId.from_sequence(
                    IdKind.PROJECT,
                    row[0] + 1,
                )
            except CanonicalIdError as exc:
                raise ProjectStoreConflictError(
                    "project canonical ID sequence is exhausted or invalid"
                ) from exc

            existing_row = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?",
                (project_id.value,),
            ).fetchone()
            if existing_row is not None:
                raise ProjectStoreConflictError(
                    "project sequence conflicts with an existing project row"
                )

            layout = ProjectLayout(self._projects_root, project_id)
            project_dir_existed = layout.project_dir.exists() or layout.project_dir.is_symlink()
            if project_dir_existed:
                raise ProjectStoreConflictError(
                    f"prospective project directory already exists: {layout.project_dir}"
                )

            connection.execute(
                "UPDATE canonical_id_sequences SET last_sequence = ? WHERE kind = ?",
                (project_id.sequence, IdKind.PROJECT.value),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ProjectStoreConflictError(
                    "project canonical ID sequence update failed"
                )
            connection.execute(
                "INSERT INTO projects(project_id) VALUES(?)",
                (project_id.value,),
            )

            layout.ensure_directories()
            metadata = _metadata_for(project_id)
            _write_project_metadata(layout.project_json, metadata)
            layout.validate_directories()
            _read_project_metadata(layout.project_json, project_id=project_id)

            connection.commit()
            return ProjectHandle(
                project_id=project_id,
                layout=layout,
                database_path=self._database_path,
                metadata=metadata,
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            if (
                layout is not None
                and not project_dir_existed
                and layout.project_dir.exists()
                and not layout.project_dir.is_symlink()
            ):
                shutil.rmtree(layout.project_dir, ignore_errors=True)
            raise
        finally:
            connection.close()

    def open_project(
        self,
        project_id: CanonicalId | str,
    ) -> ProjectHandle:
        """Open one existing project without mutating Project State."""

        try:
            canonical = (
                project_id
                if isinstance(project_id, CanonicalId)
                else CanonicalId.parse(project_id)
            )
        except CanonicalIdError as exc:
            raise ProjectNotFoundError("project ID is invalid") from exc
        if canonical.kind is not IdKind.PROJECT:
            raise ProjectNotFoundError("requested ID is not a project ID")

        layout = ProjectLayout(self._projects_root, canonical)
        try:
            layout.validate_directories()
        except ProjectLayoutError as exc:
            raise ProjectStoreConflictError(
                f"project filesystem layout is invalid: {exc}"
            ) from exc

        connection = self._connect_existing()
        try:
            try:
                verify_project_schema(connection)
            except ProjectSchemaError as exc:
                raise ProjectStoreConflictError(
                    f"Project State database schema is invalid: {exc}"
                ) from exc
            row = connection.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (canonical.value,),
            ).fetchone()
            if row is None:
                raise ProjectNotFoundError(
                    f"project is not registered: {canonical.value}"
                )
        finally:
            connection.close()

        metadata = _read_project_metadata(
            layout.project_json,
            project_id=canonical,
        )
        return ProjectHandle(
            project_id=canonical,
            layout=layout,
            database_path=self._database_path,
            metadata=metadata,
        )
