from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.assumptions import (
    ASSUMPTION_FILENAME,
    ASSUMPTION_FORMAT_VERSION,
    ASSUMPTION_HASH_FILENAME,
    MAX_ASSUMPTION_STORE_BYTES,
    AssumptionSet,
)
from ai.project.decisions import (
    DECISION_FILENAME,
    DECISION_FORMAT_VERSION,
    DECISION_HASH_FILENAME,
    MAX_DECISION_STORE_BYTES,
    DecisionSet,
)
from ai.project.facts import (
    FACT_FILENAME,
    FACT_FORMAT_VERSION,
    FACT_HASH_FILENAME,
    MAX_FACT_STORE_BYTES,
    FactSet,
)
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


PROJECT_TRANSACTION_CONTRACT_VERSION = "1.0"
MAX_TRANSACTION_CATEGORIES = 3
MAX_TRANSACTION_FILES = 6
MAX_TRANSACTION_STAGED_BYTES = 8 * 1024 * 1024
PROJECT_RECOVERY_CONTRACT_VERSION = "1.0"
PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION = 1
PROJECT_TRANSACTION_JOURNAL_FILENAME = ".project-state-transaction.json"
MAX_TRANSACTION_JOURNAL_BYTES = 12 * 1024 * 1024

_CATEGORY_ORDER: tuple[str, ...] = (
    "facts",
    "assumptions",
    "decisions",
)


_TRANSACTION_TARGET_FILENAMES = frozenset(
    {
        FACT_FILENAME,
        FACT_HASH_FILENAME,
        ASSUMPTION_FILENAME,
        ASSUMPTION_HASH_FILENAME,
        DECISION_FILENAME,
        DECISION_HASH_FILENAME,
    }
)

_JOURNAL_KEYS = frozenset(
    {
        "format_version",
        "project_id",
        "state",
        "files",
    }
)

_JOURNAL_FILE_KEYS = frozenset(
    {
        "name",
        "existed",
        "backup_b64",
        "backup_sha256",
    }
)


class ProjectTransactionState(str, Enum):
    OPEN = "OPEN"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"


class ProjectTransactionError(RuntimeError):
    """Base error for the reusable S3.9 Project State transaction wrapper."""


class ProjectTransactionStateError(ProjectTransactionError):
    """Raised when an operation is incompatible with transaction state."""


class ProjectTransactionConflictError(ProjectTransactionError):
    """Raised when staged state conflicts with project identity or storage."""


@dataclass(frozen=True)
class ProjectTransactionCommit:
    """Immutable commit receipt for deterministic tests and callers."""

    project_id: CanonicalId
    categories: tuple[str, ...]
    file_count: int
    staged_bytes: int

    def __post_init__(self) -> None:
        if self.project_id.kind is not IdKind.PROJECT:
            raise ProjectTransactionConflictError(
                "commit receipt requires a project ID"
            )
        if (
            not isinstance(self.categories, tuple)
            or any(item not in _CATEGORY_ORDER for item in self.categories)
        ):
            raise ProjectTransactionConflictError(
                "commit receipt contains an invalid category"
            )
        if len(set(self.categories)) != len(self.categories):
            raise ProjectTransactionConflictError(
                "commit receipt categories must be unique"
            )
        if (
            isinstance(self.file_count, bool)
            or not isinstance(self.file_count, int)
            or self.file_count < 1
            or self.file_count > MAX_TRANSACTION_FILES
        ):
            raise ProjectTransactionConflictError(
                "commit receipt file_count is invalid"
            )
        if (
            isinstance(self.staged_bytes, bool)
            or not isinstance(self.staged_bytes, int)
            or self.staged_bytes < 1
            or self.staged_bytes > MAX_TRANSACTION_STAGED_BYTES
        ):
            raise ProjectTransactionConflictError(
                "commit receipt staged_bytes is invalid"
            )


@dataclass(frozen=True)
class _StagedCategory:
    category: str
    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if self.category not in _CATEGORY_ORDER:
            raise ProjectTransactionConflictError(
                "invalid staged Project State category"
            )
        object.__setattr__(
            self,
            "files",
            MappingProxyType(dict(self.files)),
        )


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ProjectTransactionConflictError(
            "project_id must be a canonical project ID"
        ) from exc

    if value.kind is not IdKind.PROJECT:
        raise ProjectTransactionConflictError(
            "project_id must be a canonical project ID"
        )
    return value


def _canonical_json(
    payload: Mapping[str, object],
    *,
    limit: int,
    label: str,
) -> bytes:
    try:
        raw = (
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProjectTransactionConflictError(
            f"{label} could not be serialized"
        ) from exc

    if len(raw) > limit:
        raise ProjectTransactionConflictError(
            f"{label} exceeds its store size limit"
        )
    return raw


def _pair(
    *,
    data_filename: str,
    hash_filename: str,
    raw: bytes,
) -> Mapping[str, bytes]:
    digest = hashlib.sha256(raw).hexdigest()
    return MappingProxyType(
        {
            data_filename: raw,
            hash_filename: (digest + "\n").encode("ascii"),
        }
    )


def _encode_fact_set(value: FactSet) -> _StagedCategory:
    raw = _canonical_json(
        {
            "format_version": FACT_FORMAT_VERSION,
            "project_id": value.project_id.value,
            "facts": [item.to_dict() for item in value.facts],
        },
        limit=MAX_FACT_STORE_BYTES,
        label="FactSet",
    )
    return _StagedCategory(
        category="facts",
        files=_pair(
            data_filename=FACT_FILENAME,
            hash_filename=FACT_HASH_FILENAME,
            raw=raw,
        ),
    )


def _encode_assumption_set(value: AssumptionSet) -> _StagedCategory:
    raw = _canonical_json(
        {
            "format_version": ASSUMPTION_FORMAT_VERSION,
            "project_id": value.project_id.value,
            "assumptions": [
                item.to_dict()
                for item in value.assumptions
            ],
        },
        limit=MAX_ASSUMPTION_STORE_BYTES,
        label="AssumptionSet",
    )
    return _StagedCategory(
        category="assumptions",
        files=_pair(
            data_filename=ASSUMPTION_FILENAME,
            hash_filename=ASSUMPTION_HASH_FILENAME,
            raw=raw,
        ),
    )


def _encode_decision_set(value: DecisionSet) -> _StagedCategory:
    raw = _canonical_json(
        {
            "format_version": DECISION_FORMAT_VERSION,
            "project_id": value.project_id.value,
            "decisions": [
                item.to_dict()
                for item in value.decisions
            ],
        },
        limit=MAX_DECISION_STORE_BYTES,
        label="DecisionSet",
    )
    return _StagedCategory(
        category="decisions",
        files=_pair(
            data_filename=DECISION_FILENAME,
            hash_filename=DECISION_HASH_FILENAME,
            raw=raw,
        ),
    )


def _read_existing(path: Path, *, limit: int) -> bytes | None:
    if path.is_symlink():
        raise ProjectTransactionConflictError(
            f"transaction target must not be a symbolic link: {path.name}"
        )
    if not path.exists():
        return None
    if not path.is_file():
        raise ProjectTransactionConflictError(
            f"transaction target is not a regular file: {path.name}"
        )

    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise ProjectTransactionConflictError(
            f"transaction target could not be read: {path.name}"
        ) from exc

    if len(raw) > limit:
        raise ProjectTransactionConflictError(
            f"existing transaction target is too large: {path.name}"
        )
    return raw


def _atomic_replace(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ProjectTransactionConflictError(
            f"transaction target must not be a symbolic link: {path.name}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ProjectTransactionConflictError(
            "project directory must not be a symbolic link"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.txn.",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        fd = os.open(path, flags)
    except OSError:
        # Directory fsync support is platform/filesystem dependent. File fsync
        # has already completed, so unsupported directory fsync is non-fatal.
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)



def _journal_path(handle: ProjectHandle) -> Path:
    return handle.layout.project_dir / PROJECT_TRANSACTION_JOURNAL_FILENAME


def _strict_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectTransactionConflictError(
                f"duplicate transaction-journal JSON key: {key}"
            )
        result[key] = value
    return result


def _reject_json_constant(raw: str) -> None:
    raise ProjectTransactionConflictError(
        f"non-finite transaction-journal JSON constant: {raw}"
    )


def _encode_journal(
    *,
    project_id: CanonicalId,
    backups: Mapping[Path, bytes | None],
) -> bytes:
    files: list[dict[str, object]] = []
    total_backup_bytes = 0

    for path in sorted(backups, key=lambda item: item.name):
        if path.name not in _TRANSACTION_TARGET_FILENAMES:
            raise ProjectTransactionConflictError(
                f"journal target is not an approved Project State file: {path.name}"
            )

        previous = backups[path]
        if previous is None:
            files.append(
                {
                    "name": path.name,
                    "existed": False,
                    "backup_b64": None,
                    "backup_sha256": None,
                }
            )
            continue

        total_backup_bytes += len(previous)
        if total_backup_bytes > MAX_TRANSACTION_STAGED_BYTES:
            raise ProjectTransactionConflictError(
                "transaction rollback backup exceeds bounded size"
            )

        files.append(
            {
                "name": path.name,
                "existed": True,
                "backup_b64": base64.b64encode(previous).decode("ascii"),
                "backup_sha256": hashlib.sha256(previous).hexdigest(),
            }
        )

    payload = {
        "format_version": PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION,
        "project_id": project_id.value,
        "state": "PREPARED",
        "files": files,
    }
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

    if len(raw) > MAX_TRANSACTION_JOURNAL_BYTES:
        raise ProjectTransactionConflictError(
            "transaction recovery journal exceeds bounded size"
        )
    return raw


def _read_journal(
    handle: ProjectHandle,
) -> dict[str, object]:
    path = _journal_path(handle)

    if path.is_symlink():
        raise ProjectTransactionConflictError(
            "transaction recovery journal must not be a symbolic link"
        )
    if not path.exists():
        raise ProjectTransactionConflictError(
            "transaction recovery journal does not exist"
        )
    if not path.is_file():
        raise ProjectTransactionConflictError(
            "transaction recovery journal is not a regular file"
        )

    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_TRANSACTION_JOURNAL_BYTES + 1)
    except OSError as exc:
        raise ProjectTransactionConflictError(
            "transaction recovery journal could not be read"
        ) from exc

    if len(raw) > MAX_TRANSACTION_JOURNAL_BYTES:
        raise ProjectTransactionConflictError(
            "transaction recovery journal exceeds bounded size"
        )

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except ProjectTransactionConflictError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectTransactionConflictError(
            "transaction recovery journal is invalid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise ProjectTransactionConflictError(
            "transaction recovery journal top level must be an object"
        )
    if frozenset(payload.keys()) != _JOURNAL_KEYS:
        raise ProjectTransactionConflictError(
            "transaction recovery journal keys are invalid"
        )
    if (
        payload["format_version"]
        != PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION
    ):
        raise ProjectTransactionConflictError(
            "unsupported transaction recovery journal format"
        )
    if payload["project_id"] != handle.project_id.value:
        raise ProjectTransactionConflictError(
            "transaction recovery journal project ID mismatch"
        )
    if payload["state"] != "PREPARED":
        raise ProjectTransactionConflictError(
            "transaction recovery journal state is invalid"
        )

    files = payload["files"]
    if (
        not isinstance(files, list)
        or not 1 <= len(files) <= MAX_TRANSACTION_FILES
    ):
        raise ProjectTransactionConflictError(
            "transaction recovery journal file list is invalid"
        )

    seen: set[str] = set()
    total_backup_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise ProjectTransactionConflictError(
                "transaction recovery journal file entry must be an object"
            )
        if frozenset(item.keys()) != _JOURNAL_FILE_KEYS:
            raise ProjectTransactionConflictError(
                "transaction recovery journal file entry keys are invalid"
            )

        name = item["name"]
        existed = item["existed"]
        backup_b64 = item["backup_b64"]
        backup_sha256 = item["backup_sha256"]

        if (
            not isinstance(name, str)
            or name not in _TRANSACTION_TARGET_FILENAMES
            or name in seen
        ):
            raise ProjectTransactionConflictError(
                "transaction recovery journal target is invalid or duplicated"
            )
        seen.add(name)

        if not isinstance(existed, bool):
            raise ProjectTransactionConflictError(
                "transaction recovery journal existed flag is invalid"
            )

        if not existed:
            if backup_b64 is not None or backup_sha256 is not None:
                raise ProjectTransactionConflictError(
                    "nonexistent journal target must not contain a backup"
                )
            continue

        if (
            not isinstance(backup_b64, str)
            or not isinstance(backup_sha256, str)
            or len(backup_sha256) != 64
        ):
            raise ProjectTransactionConflictError(
                "transaction recovery journal backup metadata is invalid"
            )
        try:
            backup = base64.b64decode(
                backup_b64.encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise ProjectTransactionConflictError(
                "transaction recovery journal backup is invalid base64"
            ) from exc

        total_backup_bytes += len(backup)
        if total_backup_bytes > MAX_TRANSACTION_STAGED_BYTES:
            raise ProjectTransactionConflictError(
                "transaction recovery journal backup exceeds bounded size"
            )
        if hashlib.sha256(backup).hexdigest() != backup_sha256:
            raise ProjectTransactionConflictError(
                "transaction recovery journal backup hash mismatch"
            )

    return payload


def recover_project_state_transaction(
    project_store: ProjectStore,
    project_id: CanonicalId | str,
) -> bool:
    """Recover one interrupted S3.9 transaction by rolling back to pre-commit.

    Returns False when no journal exists. Returns True only after every target
    has been restored/deleted according to the prepared journal and the journal
    itself has been removed durably.

    Corrupt or foreign journals fail closed and are never guessed around.
    """

    if not isinstance(project_store, ProjectStore):
        raise TypeError("project_store must be ProjectStore")

    canonical = _parse_project_id(project_id)
    try:
        handle = project_store.open_project(canonical)
    except (
        ProjectNotFoundError,
        ProjectStoreConflictError,
    ) as exc:
        raise ProjectTransactionConflictError(
            f"project could not be opened for recovery: {exc}"
        ) from exc

    journal_path = _journal_path(handle)
    if not journal_path.exists() and not journal_path.is_symlink():
        return False

    payload = _read_journal(handle)
    files = payload["files"]
    assert isinstance(files, list)

    for item in files:
        assert isinstance(item, dict)
        name = item["name"]
        existed = item["existed"]
        target = handle.layout.project_dir / str(name)

        if target.is_symlink():
            raise ProjectTransactionConflictError(
                f"transaction recovery target must not be a symbolic link: {target.name}"
            )

        if existed:
            backup_b64 = item["backup_b64"]
            assert isinstance(backup_b64, str)
            backup = base64.b64decode(
                backup_b64.encode("ascii"),
                validate=True,
            )
            _atomic_replace(target, backup)
        else:
            target.unlink(missing_ok=True)

    _fsync_directory(handle.layout.project_dir)
    journal_path.unlink()
    _fsync_directory(handle.layout.project_dir)
    return True


class ProjectStateTransaction:
    """Reusable S3.9 multi-category Project State transaction wrapper.

    Scope is intentionally limited to the current snapshot categories already
    implemented by S3.6-S3.8: facts, assumptions, and decisions.

    Requirement versions remain owned by their immutable S3.5 append operation,
    including persistent REQ-ID allocation. S3.9 does not rewrite that history
    or expose arbitrary filesystem/SQL mutation.

    Commit semantics provide in-process all-or-nothing behavior: all staged
    category files are replaced, or every previously-written target is restored
    if a visible write fails. S3.10 adds a bounded prepared journal so an interrupted visible commit can
    be rolled back explicitly after restart.
    """

    def __init__(
        self,
        project_store: ProjectStore,
        project_id: CanonicalId | str,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")

        self._project_store = project_store
        self._project_id = _parse_project_id(project_id)
        self._state = ProjectTransactionState.OPEN
        self._staged: dict[str, _StagedCategory] = {}

        try:
            self._handle = project_store.open_project(self._project_id)
        except (
            ProjectNotFoundError,
            ProjectStoreConflictError,
        ) as exc:
            raise ProjectTransactionConflictError(
                f"project could not be opened: {exc}"
            ) from exc

        journal = _journal_path(self._handle)
        if journal.exists() or journal.is_symlink():
            raise ProjectTransactionConflictError(
                "pending transaction recovery journal exists; "
                "call recover_project_state_transaction() first"
            )

    @property
    def project_id(self) -> CanonicalId:
        return self._project_id

    @property
    def state(self) -> ProjectTransactionState:
        return self._state

    @property
    def staged_categories(self) -> tuple[str, ...]:
        return tuple(
            category
            for category in _CATEGORY_ORDER
            if category in self._staged
        )

    @property
    def staged_bytes(self) -> int:
        return sum(
            len(raw)
            for staged in self._staged.values()
            for raw in staged.files.values()
        )

    def _require_open(self) -> None:
        if self._state is not ProjectTransactionState.OPEN:
            raise ProjectTransactionStateError(
                "transaction is no longer OPEN"
            )

    def _stage(
        self,
        staged: _StagedCategory,
        *,
        value_project_id: CanonicalId,
    ) -> None:
        self._require_open()

        if value_project_id != self._project_id:
            raise ProjectTransactionConflictError(
                "staged Project State belongs to a different project"
            )
        if staged.category in self._staged:
            raise ProjectTransactionConflictError(
                f"category already staged: {staged.category}"
            )
        if len(self._staged) >= MAX_TRANSACTION_CATEGORIES:
            raise ProjectTransactionConflictError(
                "transaction category limit exceeded"
            )

        projected_files = sum(
            len(item.files)
            for item in self._staged.values()
        ) + len(staged.files)
        projected_bytes = self.staged_bytes + sum(
            len(raw)
            for raw in staged.files.values()
        )

        if projected_files > MAX_TRANSACTION_FILES:
            raise ProjectTransactionConflictError(
                "transaction file limit exceeded"
            )
        if projected_bytes > MAX_TRANSACTION_STAGED_BYTES:
            raise ProjectTransactionConflictError(
                "transaction staged-byte limit exceeded"
            )

        self._staged[staged.category] = staged

    def stage_facts(self, value: FactSet) -> None:
        if not isinstance(value, FactSet):
            raise TypeError("value must be FactSet")
        self._stage(
            _encode_fact_set(value),
            value_project_id=value.project_id,
        )

    def stage_assumptions(self, value: AssumptionSet) -> None:
        if not isinstance(value, AssumptionSet):
            raise TypeError("value must be AssumptionSet")
        self._stage(
            _encode_assumption_set(value),
            value_project_id=value.project_id,
        )

    def stage_decisions(self, value: DecisionSet) -> None:
        if not isinstance(value, DecisionSet):
            raise TypeError("value must be DecisionSet")
        self._stage(
            _encode_decision_set(value),
            value_project_id=value.project_id,
        )

    def commit(self) -> ProjectTransactionCommit:
        self._require_open()

        if not self._staged:
            raise ProjectTransactionStateError(
                "cannot commit an empty Project State transaction"
            )

        # Re-open immediately before mutation so existing S3.3 layout/metadata
        # validation remains the authority for the project boundary.
        try:
            handle = self._project_store.open_project(
                self._project_id
            )
        except (
            ProjectNotFoundError,
            ProjectStoreConflictError,
        ) as exc:
            raise ProjectTransactionConflictError(
                f"project could not be reopened: {exc}"
            ) from exc

        if handle.layout.project_dir != self._handle.layout.project_dir:
            raise ProjectTransactionConflictError(
                "project directory changed during transaction"
            )

        file_items: list[tuple[Path, bytes]] = []
        for category in _CATEGORY_ORDER:
            staged = self._staged.get(category)
            if staged is None:
                continue
            for filename, raw in staged.files.items():
                file_items.append(
                    (
                        handle.layout.project_dir / filename,
                        raw,
                    )
                )

        backups: dict[Path, bytes | None] = {}
        written: list[Path] = []

        # Existing snapshot/hash files are all bounded to <=2 MiB or tiny hash
        # sidecars. The global limit keeps rollback memory deterministic.
        for path, _raw in file_items:
            existing = _read_existing(
                path,
                limit=MAX_TRANSACTION_STAGED_BYTES,
            )
            backups[path] = existing

        journal_path = _journal_path(handle)
        if journal_path.exists() or journal_path.is_symlink():
            raise ProjectTransactionConflictError(
                "pending transaction recovery journal already exists"
            )

        journal_raw = _encode_journal(
            project_id=self._project_id,
            backups=backups,
        )
        _atomic_replace(journal_path, journal_raw)
        _fsync_directory(handle.layout.project_dir)

        try:
            for path, raw in file_items:
                _atomic_replace(path, raw)
                written.append(path)

            _fsync_directory(handle.layout.project_dir)
            journal_path.unlink()
            _fsync_directory(handle.layout.project_dir)
        except Exception:
            rollback_error: Exception | None = None
            for path in reversed(written):
                previous = backups[path]
                try:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_replace(path, previous)
                except Exception as exc:
                    rollback_error = rollback_error or exc

            _fsync_directory(handle.layout.project_dir)

            if rollback_error is not None:
                raise ProjectTransactionConflictError(
                    "transaction write failed and rollback was incomplete; "
                    "recovery journal retained"
                ) from rollback_error

            journal_path.unlink(missing_ok=True)
            _fsync_directory(handle.layout.project_dir)
            raise

        self._state = ProjectTransactionState.COMMITTED
        return ProjectTransactionCommit(
            project_id=self._project_id,
            categories=self.staged_categories,
            file_count=len(file_items),
            staged_bytes=self.staged_bytes,
        )

    def rollback(self) -> None:
        self._require_open()
        self._staged.clear()
        self._state = ProjectTransactionState.ROLLED_BACK

    def __enter__(self) -> "ProjectStateTransaction":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._state is ProjectTransactionState.OPEN:
            self.rollback()
        return False
