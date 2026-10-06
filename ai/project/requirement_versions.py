from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.requirements import (
    Requirement,
    RequirementContractError,
    RequirementSet,
    RequirementSetStatus,
)
from ai.project.schema import ProjectSchemaError, verify_project_schema
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


REQUIREMENT_VERSION_STORE_CONTRACT_VERSION = "1.0"
REQUIREMENT_VERSION_FORMAT_VERSION = 1
REQUIREMENT_CURRENT_FILENAME = "current.json"
MAX_REQUIREMENT_VERSION = 999_999
MAX_REQUIREMENT_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_REQUIREMENT_CURRENT_BYTES = 16 * 1024

_SNAPSHOT_RE = re.compile(r"^v([0-9]{6})\.json$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

_SNAPSHOT_KEYS = frozenset(
    {
        "format_version",
        "project_id",
        "version",
        "parent_version",
        "requirement_set",
    }
)
_CURRENT_KEYS = frozenset(
    {
        "format_version",
        "project_id",
        "latest_version",
        "snapshot_sha256",
    }
)


class RequirementVersionError(RuntimeError):
    """Base error for S3.5 requirement version persistence."""


class RequirementVersionConflictError(RequirementVersionError):
    """Raised when requirement history is partial, conflicting, or corrupt."""


class RequirementVersionNotFoundError(RequirementVersionError):
    """Raised when a requested immutable requirement version is absent."""


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise RequirementVersionNotFoundError("project ID is invalid") from exc
    if value.kind is not IdKind.PROJECT:
        raise RequirementVersionNotFoundError(
            "requirement versions require a project ID"
        )
    return value


def _parse_requirement_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise RequirementVersionConflictError(
            "requirement_id is invalid"
        ) from exc
    if value.kind is not IdKind.REQUIREMENT:
        raise RequirementVersionConflictError(
            "requirement_id must be a canonical requirement ID"
        )
    return value


def _validate_text(raw: Any) -> str:
    # Reuse the frozen S3.4 content contract instead of creating a second
    # competing text-normalization rule.
    try:
        probe = Requirement(
            requirement_id=CanonicalId.from_sequence(
                IdKind.REQUIREMENT,
                1,
            ),
            text=raw,
        )
    except RequirementContractError as exc:
        raise RequirementVersionConflictError(
            f"requirement text is invalid: {exc}"
        ) from exc
    return probe.text


@dataclass(frozen=True)
class RequirementVersionInput:
    """One item for the next full RequirementSet snapshot.

    `requirement_id=None` means a new user requirement and causes S3.5 to
    allocate the next persistent canonical REQ ID. A supplied ID must exist in
    the immediately previous version of this same project.
    """

    text: str
    requirement_id: CanonicalId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _validate_text(self.text))
        if self.requirement_id is not None:
            object.__setattr__(
                self,
                "requirement_id",
                _parse_requirement_id(self.requirement_id),
            )


@dataclass(frozen=True)
class RequirementSetVersion:
    """Immutable readable RequirementSet version."""

    version: int
    parent_version: int | None
    requirement_set: RequirementSet

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise RequirementVersionConflictError(
                "requirement version must be an integer"
            )
        if not 1 <= self.version <= MAX_REQUIREMENT_VERSION:
            raise RequirementVersionConflictError(
                "requirement version is outside the supported range"
            )
        expected_parent = None if self.version == 1 else self.version - 1
        if self.parent_version != expected_parent:
            raise RequirementVersionConflictError(
                "parent_version must reference the immediately previous version"
            )
        if not isinstance(self.requirement_set, RequirementSet):
            raise TypeError("requirement_set must be RequirementSet")

    @property
    def project_id(self) -> CanonicalId:
        return self.requirement_set.project_id

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": REQUIREMENT_VERSION_FORMAT_VERSION,
            "project_id": self.project_id.value,
            "version": self.version,
            "parent_version": self.parent_version,
            "requirement_set": self.requirement_set.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "RequirementSetVersion":
        if not isinstance(raw, Mapping):
            raise RequirementVersionConflictError(
                "requirement version payload must be an object"
            )
        keys = frozenset(raw.keys())
        if keys != _SNAPSHOT_KEYS:
            raise RequirementVersionConflictError(
                "requirement version payload keys do not match S3.5 contract"
            )
        if raw["format_version"] != REQUIREMENT_VERSION_FORMAT_VERSION:
            raise RequirementVersionConflictError(
                "unsupported requirement version format"
            )
        try:
            requirement_set = RequirementSet.from_dict(raw["requirement_set"])
        except RequirementContractError as exc:
            raise RequirementVersionConflictError(
                f"stored RequirementSet is invalid: {exc}"
            ) from exc

        if raw["project_id"] != requirement_set.project_id.value:
            raise RequirementVersionConflictError(
                "requirement version project ID mismatch"
            )

        return cls(
            version=raw["version"],
            parent_version=raw["parent_version"],
            requirement_set=requirement_set,
        )


def _reject_json_constant(raw: str) -> None:
    raise RequirementVersionConflictError(
        f"non-finite JSON constant is forbidden: {raw}"
    )


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RequirementVersionConflictError(
                f"duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _load_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequirementVersionConflictError(
            f"{label} is not valid UTF-8"
        ) from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except RequirementVersionConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise RequirementVersionConflictError(
            f"{label} is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RequirementVersionConflictError(
            f"{label} top level must be an object"
        )
    return payload


def _read_bounded_file(
    path: Path,
    *,
    limit: int,
    label: str,
) -> bytes:
    if path.is_symlink():
        raise RequirementVersionConflictError(
            f"{label} must not be a symbolic link"
        )
    if not path.exists():
        raise RequirementVersionNotFoundError(
            f"{label} does not exist"
        )
    if not path.is_file():
        raise RequirementVersionConflictError(
            f"{label} is not a regular file"
        )
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise RequirementVersionConflictError(
            f"{label} could not be read"
        ) from exc
    if len(raw) > limit:
        raise RequirementVersionConflictError(
            f"{label} exceeds its size limit"
        )
    return raw


def _encode_json(payload: Mapping[str, Any], *, limit: int) -> bytes:
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > limit:
        raise RequirementVersionConflictError(
            "serialized requirement version exceeds its size limit"
        )
    return encoded


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _snapshot_name(version: int) -> str:
    if isinstance(version, bool) or not isinstance(version, int):
        raise RequirementVersionNotFoundError(
            "requirement version must be an integer"
        )
    if not 1 <= version <= MAX_REQUIREMENT_VERSION:
        raise RequirementVersionNotFoundError(
            "requirement version is outside the supported range"
        )
    return f"v{version:06d}.json"


def _snapshot_path(handle: ProjectHandle, version: int) -> Path:
    return handle.layout.data_directories["requirements"] / _snapshot_name(version)


def _hash_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(snapshot_path.name + ".sha256")


def _current_path(handle: ProjectHandle) -> Path:
    return (
        handle.layout.data_directories["requirements"]
        / REQUIREMENT_CURRENT_FILENAME
    )


def _atomic_write_new(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise RequirementVersionConflictError(
            f"immutable requirement version path already exists: {path.name}"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        # Do not overwrite a concurrently-created immutable version.
        if path.exists() or path.is_symlink():
            raise RequirementVersionConflictError(
                f"immutable requirement version path already exists: {path.name}"
            )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_replace(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RequirementVersionConflictError(
            f"{path.name} must not be a symbolic link"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _parse_current(
    raw: bytes,
    *,
    project_id: CanonicalId,
) -> tuple[int, str]:
    payload = _load_json_object(
        raw,
        label="requirement current pointer",
    )
    if frozenset(payload.keys()) != _CURRENT_KEYS:
        raise RequirementVersionConflictError(
            "requirement current pointer keys do not match S3.5 contract"
        )
    if payload["format_version"] != REQUIREMENT_VERSION_FORMAT_VERSION:
        raise RequirementVersionConflictError(
            "unsupported requirement current-pointer format"
        )
    if payload["project_id"] != project_id.value:
        raise RequirementVersionConflictError(
            "requirement current pointer project ID mismatch"
        )

    version = payload["latest_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= MAX_REQUIREMENT_VERSION
    ):
        raise RequirementVersionConflictError(
            "requirement current pointer version is invalid"
        )
    digest = payload["snapshot_sha256"]
    if not isinstance(digest, str) or _HASH_RE.fullmatch(digest) is None:
        raise RequirementVersionConflictError(
            "requirement current pointer hash is invalid"
        )
    return version, digest


def _read_hash(path: Path) -> str:
    raw = _read_bounded_file(
        path,
        limit=128,
        label="requirement snapshot hash",
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RequirementVersionConflictError(
            "requirement snapshot hash is not ASCII"
        ) from exc
    if not text.endswith("\n"):
        raise RequirementVersionConflictError(
            "requirement snapshot hash is not canonical"
        )
    digest = text[:-1]
    if _HASH_RE.fullmatch(digest) is None:
        raise RequirementVersionConflictError(
            "requirement snapshot hash is invalid"
        )
    return digest


def _read_version_from_handle(
    handle: ProjectHandle,
    version: int,
) -> tuple[RequirementSetVersion, str]:
    snapshot_path = _snapshot_path(handle, version)
    raw = _read_bounded_file(
        snapshot_path,
        limit=MAX_REQUIREMENT_SNAPSHOT_BYTES,
        label="requirement snapshot",
    )
    expected_hash = _read_hash(_hash_path(snapshot_path))
    actual_hash = _sha256_bytes(raw)
    if actual_hash != expected_hash:
        raise RequirementVersionConflictError(
            "requirement snapshot hash mismatch"
        )

    parsed = RequirementSetVersion.from_dict(
        _load_json_object(
            raw,
            label="requirement snapshot",
        )
    )
    if parsed.version != version:
        raise RequirementVersionConflictError(
            "requirement snapshot version/path mismatch"
        )
    if parsed.project_id != handle.project_id:
        raise RequirementVersionConflictError(
            "requirement snapshot project mismatch"
        )
    return parsed, actual_hash


class RequirementVersionStore:
    """S3.5 immutable RequirementSet history for a ProjectStore.

    SQLite remains responsible for persistent canonical REQ-ID allocation.
    The project `requirements/` directory stores immutable full snapshots plus
    their hashes and one bounded `current.json` pointer.

    This class is operation-specific and intentionally does not expose the
    general transaction wrapper reserved for S3.9.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_handle(
        self,
        project_id: CanonicalId | str,
    ) -> ProjectHandle:
        canonical = _parse_project_id(project_id)
        try:
            return self._project_store.open_project(canonical)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise RequirementVersionConflictError(
                f"project could not be opened: {exc}"
            ) from exc

    def _connect_verified(
        self,
        handle: ProjectHandle,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(handle.database_path)
        try:
            verify_project_schema(connection)
        except ProjectSchemaError as exc:
            connection.close()
            raise RequirementVersionConflictError(
                f"Project State schema is invalid: {exc}"
            ) from exc
        return connection

    def open_version(
        self,
        project_id: CanonicalId | str,
        version: int,
    ) -> RequirementSetVersion:
        handle = self._open_handle(project_id)
        parsed, _digest = _read_version_from_handle(handle, version)
        return parsed

    def open_latest(
        self,
        project_id: CanonicalId | str,
    ) -> RequirementSetVersion:
        handle = self._open_handle(project_id)
        current = _current_path(handle)
        raw = _read_bounded_file(
            current,
            limit=MAX_REQUIREMENT_CURRENT_BYTES,
            label="requirement current pointer",
        )
        version, expected_hash = _parse_current(
            raw,
            project_id=handle.project_id,
        )
        parsed, actual_hash = _read_version_from_handle(
            handle,
            version,
        )
        if actual_hash != expected_hash:
            raise RequirementVersionConflictError(
                "requirement current pointer hash mismatch"
            )
        return parsed

    def create_initial(
        self,
        project_id: CanonicalId | str,
        inputs: Iterable[RequirementVersionInput | str],
        *,
        status: RequirementSetStatus | str = RequirementSetStatus.DRAFT,
    ) -> RequirementSetVersion:
        handle = self._open_handle(project_id)
        materialized = tuple(
            item
            if isinstance(item, RequirementVersionInput)
            else RequirementVersionInput(text=item)
            for item in inputs
        )
        if any(item.requirement_id is not None for item in materialized):
            raise RequirementVersionConflictError(
                "initial requirement version must allocate all requirement IDs"
            )
        return self._append(
            handle,
            materialized,
            status=status,
            require_existing=False,
        )

    def append_version(
        self,
        project_id: CanonicalId | str,
        inputs: Iterable[RequirementVersionInput | str],
        *,
        status: RequirementSetStatus | str = RequirementSetStatus.DRAFT,
    ) -> RequirementSetVersion:
        handle = self._open_handle(project_id)
        materialized = tuple(
            item
            if isinstance(item, RequirementVersionInput)
            else RequirementVersionInput(text=item)
            for item in inputs
        )
        return self._append(
            handle,
            materialized,
            status=status,
            require_existing=True,
        )

    def _append(
        self,
        handle: ProjectHandle,
        inputs: tuple[RequirementVersionInput, ...],
        *,
        status: RequirementSetStatus | str,
        require_existing: bool,
    ) -> RequirementSetVersion:
        requirements_dir = handle.layout.data_directories["requirements"]
        if requirements_dir.is_symlink() or not requirements_dir.is_dir():
            raise RequirementVersionConflictError(
                "requirements directory is invalid"
            )

        current_path = _current_path(handle)
        connection = self._connect_verified(handle)

        snapshot_path: Path | None = None
        hash_path: Path | None = None
        old_current: bytes | None = None
        current_existed = False

        try:
            connection.execute("BEGIN IMMEDIATE")

            previous: RequirementSetVersion | None = None
            if current_path.exists() or current_path.is_symlink():
                current_existed = True
                old_current = _read_bounded_file(
                    current_path,
                    limit=MAX_REQUIREMENT_CURRENT_BYTES,
                    label="requirement current pointer",
                )
                latest_version, latest_hash = _parse_current(
                    old_current,
                    project_id=handle.project_id,
                )
                previous, actual_hash = _read_version_from_handle(
                    handle,
                    latest_version,
                )
                if latest_hash != actual_hash:
                    raise RequirementVersionConflictError(
                        "requirement current pointer hash mismatch"
                    )
            elif require_existing:
                raise RequirementVersionNotFoundError(
                    "no previous requirement version exists"
                )
            else:
                # Initial creation must not adopt orphan history.
                orphans = tuple(
                    path.name
                    for path in requirements_dir.iterdir()
                    if (
                        _SNAPSHOT_RE.fullmatch(path.name)
                        or path.name.endswith(".json.sha256")
                    )
                )
                if orphans:
                    raise RequirementVersionConflictError(
                        "orphan requirement-version files exist"
                    )

            if not require_existing and previous is not None:
                raise RequirementVersionConflictError(
                    "initial requirement version already exists"
                )

            version = 1 if previous is None else previous.version + 1
            if version > MAX_REQUIREMENT_VERSION:
                raise RequirementVersionConflictError(
                    "requirement version sequence is exhausted"
                )

            previous_ids = (
                set()
                if previous is None
                else {
                    item.requirement_id.value
                    for item in previous.requirement_set.requirements
                }
            )

            supplied_ids = [
                item.requirement_id.value
                for item in inputs
                if item.requirement_id is not None
            ]
            if len(supplied_ids) != len(set(supplied_ids)):
                raise RequirementVersionConflictError(
                    "duplicate requirement ID in next version"
                )
            unknown = sorted(set(supplied_ids) - previous_ids)
            if unknown:
                raise RequirementVersionConflictError(
                    "supplied requirement ID is not present in the previous "
                    f"version: {unknown[0]}"
                )

            new_count = sum(
                1
                for item in inputs
                if item.requirement_id is None
            )
            row = connection.execute(
                "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
                (IdKind.REQUIREMENT.value,),
            ).fetchone()
            if (
                row is None
                or isinstance(row[0], bool)
                or not isinstance(row[0], int)
                or row[0] < 0
            ):
                raise RequirementVersionConflictError(
                    "requirement canonical ID sequence row is invalid"
                )

            last_sequence = row[0]
            allocated: list[CanonicalId] = []
            for offset in range(1, new_count + 1):
                try:
                    allocated.append(
                        CanonicalId.from_sequence(
                            IdKind.REQUIREMENT,
                            last_sequence + offset,
                        )
                    )
                except CanonicalIdError as exc:
                    raise RequirementVersionConflictError(
                        "requirement canonical ID sequence is exhausted"
                    ) from exc

            allocation_index = 0
            requirements: list[Requirement] = []
            for item in inputs:
                requirement_id = item.requirement_id
                if requirement_id is None:
                    requirement_id = allocated[allocation_index]
                    allocation_index += 1
                try:
                    requirements.append(
                        Requirement(
                            requirement_id=requirement_id,
                            text=item.text,
                        )
                    )
                except RequirementContractError as exc:
                    raise RequirementVersionConflictError(
                        f"next RequirementSet is invalid: {exc}"
                    ) from exc

            try:
                requirement_set = RequirementSet(
                    project_id=handle.project_id,
                    status=status,
                    requirements=tuple(requirements),
                )
            except RequirementContractError as exc:
                raise RequirementVersionConflictError(
                    f"next RequirementSet is invalid: {exc}"
                ) from exc

            result = RequirementSetVersion(
                version=version,
                parent_version=None if version == 1 else version - 1,
                requirement_set=requirement_set,
            )

            snapshot_raw = _encode_json(
                result.to_dict(),
                limit=MAX_REQUIREMENT_SNAPSHOT_BYTES,
            )
            digest = _sha256_bytes(snapshot_raw)
            snapshot_path = _snapshot_path(handle, version)
            hash_path = _hash_path(snapshot_path)

            current_raw = _encode_json(
                {
                    "format_version": REQUIREMENT_VERSION_FORMAT_VERSION,
                    "project_id": handle.project_id.value,
                    "latest_version": version,
                    "snapshot_sha256": digest,
                },
                limit=MAX_REQUIREMENT_CURRENT_BYTES,
            )

            if new_count:
                connection.execute(
                    """
                    UPDATE canonical_id_sequences
                    SET last_sequence=?
                    WHERE kind=?
                    """,
                    (
                        last_sequence + new_count,
                        IdKind.REQUIREMENT.value,
                    ),
                )
                if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0] != 1:
                    raise RequirementVersionConflictError(
                        "requirement canonical ID sequence update failed"
                    )

            _atomic_write_new(snapshot_path, snapshot_raw)
            _atomic_write_new(
                hash_path,
                (digest + "\n").encode("ascii"),
            )
            _atomic_replace(current_path, current_raw)

            connection.commit()
            return result
        except Exception:
            if connection.in_transaction:
                connection.rollback()

            # Operation-local rollback only. The reusable transaction abstraction
            # remains explicitly gated to S3.9.
            if snapshot_path is not None:
                snapshot_path.unlink(missing_ok=True)
            if hash_path is not None:
                hash_path.unlink(missing_ok=True)

            if current_existed and old_current is not None:
                try:
                    _atomic_replace(current_path, old_current)
                except Exception:
                    pass
            elif not current_existed:
                current_path.unlink(missing_ok=True)
            raise
        finally:
            connection.close()
