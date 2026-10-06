from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias
import tempfile

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


DECISION_CONTRACT_VERSION = "1.0"
DECISION_STORE_CONTRACT_VERSION = "1.0"
DECISION_FORMAT_VERSION = 1
DECISION_FILENAME = "decisions.json"
DECISION_HASH_FILENAME = "decisions.json.sha256"

MAX_DECISIONS_PER_SET = 256
MAX_DECISION_KEY_CHARS = 128
MAX_DECISION_RATIONALE_BYTES = 16 * 1024
MAX_DECISION_JSON_DEPTH = 16
MAX_DECISION_STORE_BYTES = 2 * 1024 * 1024

_DECISION_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_DECISION_KEYS = frozenset({"key", "value", "rationale"})
_DECISION_SET_KEYS = frozenset({"project_id", "decisions"})
_DECISION_FILE_KEYS = frozenset(
    {"format_version", "project_id", "decisions"}
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


class DecisionContractError(ValueError):
    """Raised when a project decision violates the S3.8 contract."""


class DecisionStoreError(RuntimeError):
    """Base error for persistent S3.8 decision storage."""


class DecisionStoreConflictError(DecisionStoreError):
    """Raised when decision storage is partial, corrupt, or mismatched."""


class DecisionSetNotFoundError(DecisionStoreError):
    """Raised when a project has no persisted S3.8 DecisionSet."""


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise DecisionContractError(
            "project_id must be a valid canonical project ID"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise DecisionContractError(
            "project_id must be a canonical project ID"
        )
    return value


def _require_key(raw: Any) -> str:
    if not isinstance(raw, str):
        raise DecisionContractError("decision key must be a string")
    if not raw or raw != raw.strip():
        raise DecisionContractError(
            "decision key must be clean non-empty text"
        )
    if (
        len(raw) > MAX_DECISION_KEY_CHARS
        or _DECISION_KEY_RE.fullmatch(raw) is None
    ):
        raise DecisionContractError(
            "decision key must match [A-Za-z][A-Za-z0-9_.-]{0,127}"
        )
    return raw


def _require_rationale(raw: Any) -> str:
    if not isinstance(raw, str):
        raise DecisionContractError(
            "decision rationale must be a string"
        )
    if not raw or raw != raw.strip():
        raise DecisionContractError(
            "decision rationale must be clean non-empty text"
        )
    if "\x00" in raw:
        raise DecisionContractError(
            "decision rationale must not contain NUL"
        )
    if len(raw.encode("utf-8")) > MAX_DECISION_RATIONALE_BYTES:
        raise DecisionContractError(
            "decision rationale exceeds MAX_DECISION_RATIONALE_BYTES"
        )
    return raw


def _freeze_json(
    value: Any,
    *,
    path: str,
    depth: int = 0,
) -> JSONValue:
    if depth > MAX_DECISION_JSON_DEPTH:
        raise DecisionContractError(
            f"{path} exceeds MAX_DECISION_JSON_DEPTH"
        )

    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, int) and not isinstance(value, bool):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise DecisionContractError(
                f"{path} contains a non-finite number"
            )
        return value

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value)
        )

    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise DecisionContractError(
                    f"{path} contains an invalid object key"
                )
            if key in frozen:
                raise DecisionContractError(
                    f"{path} contains a duplicate object key"
                )
            frozen[key] = _freeze_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(frozen)

    raise DecisionContractError(
        f"{path} contains unsupported JSON value type "
        f"{type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _thaw_json(item)
            for key, item in value.items()
        }
    return value


@dataclass(frozen=True)
class Decision:
    """One explicit recorded project choice.

    Decisions are a separate Project State category. S3.8 deliberately does
    not invent a DECISION provenance value because the frozen provenance
    taxonomy contains no such classification, and it does not treat a decision
    as a fact, assumption, or AI proposal.

    `rationale` records why the choice was made. S3.8 does not decide who has
    authority to approve or create a decision; that remains outside this
    persistence contract.
    """

    key: str
    value: JSONValue
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "key",
            _require_key(self.key),
        )
        object.__setattr__(
            self,
            "value",
            _freeze_json(
                self.value,
                path=f"decision[{self.key}].value",
            ),
        )
        object.__setattr__(
            self,
            "rationale",
            _require_rationale(self.rationale),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "Decision":
        if not isinstance(raw, Mapping):
            raise DecisionContractError(
                "decision payload must be an object"
            )
        if frozenset(raw.keys()) != _DECISION_KEYS:
            raise DecisionContractError(
                "decision payload must contain exactly "
                "key,value,rationale"
            )
        return cls(
            key=raw["key"],
            value=raw["value"],
            rationale=raw["rationale"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _thaw_json(self.value),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class DecisionSet:
    project_id: CanonicalId
    decisions: tuple[Decision, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            _parse_project_id(self.project_id),
        )

        if not isinstance(self.decisions, tuple):
            try:
                object.__setattr__(
                    self,
                    "decisions",
                    tuple(self.decisions),
                )
            except TypeError as exc:
                raise DecisionContractError(
                    "decisions must be an iterable of Decision"
                ) from exc

        if len(self.decisions) > MAX_DECISIONS_PER_SET:
            raise DecisionContractError(
                "decisions exceeds MAX_DECISIONS_PER_SET"
            )

        seen: set[str] = set()
        for index, item in enumerate(self.decisions):
            if not isinstance(item, Decision):
                raise DecisionContractError(
                    f"decisions[{index}] must be Decision"
                )
            if item.key in seen:
                raise DecisionContractError(
                    f"duplicate decision key: {item.key}"
                )
            seen.add(item.key)

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "DecisionSet":
        if not isinstance(raw, Mapping):
            raise DecisionContractError(
                "DecisionSet payload must be an object"
            )
        if frozenset(raw.keys()) != _DECISION_SET_KEYS:
            raise DecisionContractError(
                "DecisionSet payload must contain exactly "
                "project_id,decisions"
            )

        raw_decisions = raw["decisions"]
        if not isinstance(raw_decisions, (list, tuple)):
            raise DecisionContractError(
                "decisions must be an array"
            )
        if len(raw_decisions) > MAX_DECISIONS_PER_SET:
            raise DecisionContractError(
                "decisions exceeds MAX_DECISIONS_PER_SET"
            )

        return cls(
            project_id=raw["project_id"],
            decisions=tuple(
                Decision.from_dict(item)
                for item in raw_decisions
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id.value,
            "decisions": [
                item.to_dict()
                for item in self.decisions
            ],
        }


def _reject_json_constant(raw: str) -> None:
    raise DecisionStoreConflictError(
        f"non-finite JSON constant is forbidden: {raw}"
    )


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DecisionStoreConflictError(
                f"duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _load_json_object(
    raw: bytes,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecisionStoreConflictError(
            f"{label} is not UTF-8"
        ) from exc

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except DecisionStoreConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise DecisionStoreConflictError(
            f"{label} is not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise DecisionStoreConflictError(
            f"{label} top level must be an object"
        )
    return payload


def _read_bounded(
    path: Path,
    *,
    limit: int,
    label: str,
) -> bytes:
    if path.is_symlink():
        raise DecisionStoreConflictError(
            f"{label} must not be a symbolic link"
        )
    if not path.exists():
        raise DecisionSetNotFoundError(
            f"{label} does not exist"
        )
    if not path.is_file():
        raise DecisionStoreConflictError(
            f"{label} is not a regular file"
        )

    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise DecisionStoreConflictError(
            f"{label} could not be read"
        ) from exc

    if len(raw) > limit:
        raise DecisionStoreConflictError(
            f"{label} exceeds its size limit"
        )
    return raw


def _encode(
    payload: Mapping[str, Any],
    *,
    limit: int,
) -> bytes:
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
        raise DecisionStoreConflictError(
            "serialized DecisionSet exceeds size limit"
        )
    return encoded


def _atomic_replace(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise DecisionStoreConflictError(
            f"{path.name} must not be a symbolic link"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
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
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_hash(path: Path) -> str:
    raw = _read_bounded(
        path,
        limit=128,
        label="decision-set hash",
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise DecisionStoreConflictError(
            "decision-set hash is not ASCII"
        ) from exc

    if not text.endswith("\n"):
        raise DecisionStoreConflictError(
            "decision-set hash is not canonical"
        )

    digest = text[:-1]
    if _HASH_RE.fullmatch(digest) is None:
        raise DecisionStoreConflictError(
            "decision-set hash is invalid"
        )
    return digest


class DecisionStore:
    """Persist one current DecisionSet per project.

    S3.8 records decisions but does not create decision authority, a decision
    workflow, decision IDs, decision version history, or dependency invalidation.
    General multi-record transaction composition remains S3.9.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_handle(
        self,
        project_id: CanonicalId | str,
    ) -> ProjectHandle:
        try:
            canonical = _parse_project_id(project_id)
            return self._project_store.open_project(canonical)
        except DecisionContractError as exc:
            raise DecisionStoreConflictError(str(exc)) from exc
        except (
            ProjectNotFoundError,
            ProjectStoreConflictError,
        ) as exc:
            raise DecisionStoreConflictError(
                f"project could not be opened: {exc}"
            ) from exc

    def _paths(
        self,
        handle: ProjectHandle,
    ) -> tuple[Path, Path]:
        return (
            handle.layout.project_dir / DECISION_FILENAME,
            handle.layout.project_dir / DECISION_HASH_FILENAME,
        )

    def save(
        self,
        decision_set: DecisionSet,
    ) -> DecisionSet:
        if not isinstance(decision_set, DecisionSet):
            raise TypeError(
                "decision_set must be DecisionSet"
            )

        handle = self._open_handle(
            decision_set.project_id
        )
        if handle.project_id != decision_set.project_id:
            raise DecisionStoreConflictError(
                "DecisionSet project ID mismatch"
            )

        decision_path, hash_path = self._paths(handle)

        payload = {
            "format_version": DECISION_FORMAT_VERSION,
            "project_id": decision_set.project_id.value,
            "decisions": [
                item.to_dict()
                for item in decision_set.decisions
            ],
        }
        raw = _encode(
            payload,
            limit=MAX_DECISION_STORE_BYTES,
        )
        digest = _hash_bytes(raw)
        hash_raw = (digest + "\n").encode("ascii")

        if decision_path.is_symlink() or hash_path.is_symlink():
            raise DecisionStoreConflictError(
                "decision store paths must not be symbolic links"
            )

        old_decision = (
            decision_path.read_bytes()
            if decision_path.is_file()
            else None
        )
        old_hash = (
            hash_path.read_bytes()
            if hash_path.is_file()
            else None
        )

        try:
            _atomic_replace(decision_path, raw)
            _atomic_replace(hash_path, hash_raw)
        except Exception:
            try:
                if old_decision is None:
                    decision_path.unlink(missing_ok=True)
                else:
                    _atomic_replace(
                        decision_path,
                        old_decision,
                    )

                if old_hash is None:
                    hash_path.unlink(missing_ok=True)
                else:
                    _atomic_replace(
                        hash_path,
                        old_hash,
                    )
            except Exception:
                pass
            raise

        return decision_set

    def open(
        self,
        project_id: CanonicalId | str,
    ) -> DecisionSet:
        handle = self._open_handle(project_id)
        decision_path, hash_path = self._paths(handle)

        raw = _read_bounded(
            decision_path,
            limit=MAX_DECISION_STORE_BYTES,
            label="decision set",
        )
        expected = _read_hash(hash_path)
        actual = _hash_bytes(raw)

        if actual != expected:
            raise DecisionStoreConflictError(
                "decision-set hash mismatch"
            )

        payload = _load_json_object(
            raw,
            label="decision set",
        )
        if frozenset(payload.keys()) != _DECISION_FILE_KEYS:
            raise DecisionStoreConflictError(
                "decision-set file keys do not match S3.8 contract"
            )
        if payload["format_version"] != DECISION_FORMAT_VERSION:
            raise DecisionStoreConflictError(
                "unsupported decision-set format"
            )
        if payload["project_id"] != handle.project_id.value:
            raise DecisionStoreConflictError(
                "decision-set project ID mismatch"
            )

        try:
            return DecisionSet.from_dict(
                {
                    "project_id": payload["project_id"],
                    "decisions": payload["decisions"],
                }
            )
        except DecisionContractError as exc:
            raise DecisionStoreConflictError(
                f"stored DecisionSet is invalid: {exc}"
            ) from exc
