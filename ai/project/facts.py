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
from ai.contracts.provenance import (
    ProvenanceClass,
    ProvenanceError,
    parse_provenance_class,
)
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


FACT_CONTRACT_VERSION = "1.0"
FACT_STORE_CONTRACT_VERSION = "1.0"
FACT_FORMAT_VERSION = 1
FACT_FILENAME = "facts.json"
FACT_HASH_FILENAME = "facts.json.sha256"
MAX_FACTS_PER_SET = 256
MAX_FACT_KEY_CHARS = 128
MAX_FACT_JSON_DEPTH = 16
MAX_FACT_STORE_BYTES = 2 * 1024 * 1024

_FACT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_FACT_KEYS = frozenset({"key", "value", "provenance"})
_FACT_SET_KEYS = frozenset({"project_id", "facts"})
_FACT_FILE_KEYS = frozenset({"format_version", "project_id", "facts"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


class FactContractError(ValueError):
    """Raised when a fact violates the S3.6 Project State contract."""


class FactStoreError(RuntimeError):
    """Base error for persistent S3.6 fact storage."""


class FactStoreConflictError(FactStoreError):
    """Raised when fact storage is partial, corrupt, or mismatched."""


class FactSetNotFoundError(FactStoreError):
    """Raised when a project has no persisted S3.6 fact set."""


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise FactContractError("project_id must be a valid canonical project ID") from exc
    if value.kind is not IdKind.PROJECT:
        raise FactContractError("project_id must be a canonical project ID")
    return value


def _require_key(raw: Any) -> str:
    if not isinstance(raw, str):
        raise FactContractError("fact key must be a string")
    if not raw or raw != raw.strip():
        raise FactContractError("fact key must be clean non-empty text")
    if len(raw) > MAX_FACT_KEY_CHARS or _FACT_KEY_RE.fullmatch(raw) is None:
        raise FactContractError(
            "fact key must match [A-Za-z][A-Za-z0-9_.-]{0,127}"
        )
    return raw


def _parse_fact_provenance(raw: ProvenanceClass | str) -> ProvenanceClass:
    try:
        value = parse_provenance_class(raw)
    except (ProvenanceError, TypeError) as exc:
        raise FactContractError("fact provenance is invalid") from exc
    if value not in {
        ProvenanceClass.USER_FACT,
        ProvenanceClass.VERIFIED_FACT,
    }:
        raise FactContractError(
            "fact provenance must be USER_FACT or VERIFIED_FACT"
        )
    return value


def _freeze_json(value: Any, *, path: str, depth: int = 0) -> JSONValue:
    if depth > MAX_FACT_JSON_DEPTH:
        raise FactContractError(
            f"{path} exceeds MAX_FACT_JSON_DEPTH"
        )
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FactContractError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise FactContractError(
                    f"{path} contains an invalid object key"
                )
            if key in frozen:
                raise FactContractError(
                    f"{path} contains a duplicate object key"
                )
            frozen[key] = _freeze_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(frozen)
    raise FactContractError(
        f"{path} contains unsupported JSON value type {type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class Fact:
    """One immutable Project State fact with explicit provenance.

    S3.6 represents classification; it does not grant verification authority.
    In particular, USER_FACT is never promoted to VERIFIED_FACT by this type.
    """

    key: str
    value: JSONValue
    provenance: ProvenanceClass

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_key(self.key))
        object.__setattr__(
            self,
            "value",
            _freeze_json(self.value, path=f"fact[{self.key}].value"),
        )
        object.__setattr__(
            self,
            "provenance",
            _parse_fact_provenance(self.provenance),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Fact":
        if not isinstance(raw, Mapping):
            raise FactContractError("fact payload must be an object")
        if frozenset(raw.keys()) != _FACT_KEYS:
            raise FactContractError(
                "fact payload must contain exactly key,value,provenance"
            )
        return cls(
            key=raw["key"],
            value=raw["value"],
            provenance=raw["provenance"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _thaw_json(self.value),
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True)
class FactSet:
    project_id: CanonicalId
    facts: tuple[Fact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _parse_project_id(self.project_id))
        if not isinstance(self.facts, tuple):
            try:
                object.__setattr__(self, "facts", tuple(self.facts))
            except TypeError as exc:
                raise FactContractError("facts must be an iterable of Fact") from exc
        if len(self.facts) > MAX_FACTS_PER_SET:
            raise FactContractError("facts exceeds MAX_FACTS_PER_SET")

        seen: set[str] = set()
        for index, item in enumerate(self.facts):
            if not isinstance(item, Fact):
                raise FactContractError(f"facts[{index}] must be Fact")
            if item.key in seen:
                raise FactContractError(f"duplicate fact key: {item.key}")
            seen.add(item.key)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FactSet":
        if not isinstance(raw, Mapping):
            raise FactContractError("FactSet payload must be an object")
        if frozenset(raw.keys()) != _FACT_SET_KEYS:
            raise FactContractError(
                "FactSet payload must contain exactly project_id,facts"
            )
        raw_facts = raw["facts"]
        if not isinstance(raw_facts, (list, tuple)):
            raise FactContractError("facts must be an array")
        if len(raw_facts) > MAX_FACTS_PER_SET:
            raise FactContractError("facts exceeds MAX_FACTS_PER_SET")
        return cls(
            project_id=raw["project_id"],
            facts=tuple(Fact.from_dict(item) for item in raw_facts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id.value,
            "facts": [item.to_dict() for item in self.facts],
        }


def _reject_json_constant(raw: str) -> None:
    raise FactStoreConflictError(
        f"non-finite JSON constant is forbidden: {raw}"
    )


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FactStoreConflictError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FactStoreConflictError(f"{label} is not UTF-8") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except FactStoreConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise FactStoreConflictError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FactStoreConflictError(f"{label} top level must be an object")
    return payload


def _read_bounded(path: Path, *, limit: int, label: str) -> bytes:
    if path.is_symlink():
        raise FactStoreConflictError(f"{label} must not be a symbolic link")
    if not path.exists():
        raise FactSetNotFoundError(f"{label} does not exist")
    if not path.is_file():
        raise FactStoreConflictError(f"{label} is not a regular file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise FactStoreConflictError(f"{label} could not be read") from exc
    if len(raw) > limit:
        raise FactStoreConflictError(f"{label} exceeds its size limit")
    return raw


def _encode(payload: Mapping[str, Any], *, limit: int) -> bytes:
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
        raise FactStoreConflictError("serialized fact set exceeds size limit")
    return encoded


def _atomic_replace(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise FactStoreConflictError(
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
    raw = _read_bounded(path, limit=128, label="fact-set hash")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FactStoreConflictError("fact-set hash is not ASCII") from exc
    if not text.endswith("\n"):
        raise FactStoreConflictError("fact-set hash is not canonical")
    digest = text[:-1]
    if _HASH_RE.fullmatch(digest) is None:
        raise FactStoreConflictError("fact-set hash is invalid")
    return digest


class FactStore:
    """Persist one current FactSet per project without changing SQLite schema.

    Requirements are explicitly versioned by S3.5. Facts are not silently
    promoted or versioned by S3.6. A save replaces only the current fact-set
    snapshot atomically and preserves each fact's explicit provenance.

    The reusable cross-Project-State transaction abstraction remains S3.9.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_handle(self, project_id: CanonicalId | str) -> ProjectHandle:
        try:
            canonical = _parse_project_id(project_id)
            return self._project_store.open_project(canonical)
        except FactContractError as exc:
            raise FactStoreConflictError(str(exc)) from exc
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise FactStoreConflictError(
                f"project could not be opened: {exc}"
            ) from exc

    def _paths(self, handle: ProjectHandle) -> tuple[Path, Path]:
        return (
            handle.layout.project_dir / FACT_FILENAME,
            handle.layout.project_dir / FACT_HASH_FILENAME,
        )

    def save(self, fact_set: FactSet) -> FactSet:
        if not isinstance(fact_set, FactSet):
            raise TypeError("fact_set must be FactSet")
        handle = self._open_handle(fact_set.project_id)
        if handle.project_id != fact_set.project_id:
            raise FactStoreConflictError("FactSet project ID mismatch")

        fact_path, hash_path = self._paths(handle)
        payload = {
            "format_version": FACT_FORMAT_VERSION,
            "project_id": fact_set.project_id.value,
            "facts": [item.to_dict() for item in fact_set.facts],
        }
        raw = _encode(payload, limit=MAX_FACT_STORE_BYTES)
        digest = _hash_bytes(raw)
        hash_raw = (digest + "\n").encode("ascii")

        old_fact = fact_path.read_bytes() if fact_path.is_file() and not fact_path.is_symlink() else None
        old_hash = hash_path.read_bytes() if hash_path.is_file() and not hash_path.is_symlink() else None
        if fact_path.is_symlink() or hash_path.is_symlink():
            raise FactStoreConflictError("fact store paths must not be symbolic links")

        try:
            _atomic_replace(fact_path, raw)
            _atomic_replace(hash_path, hash_raw)
        except Exception:
            # Operation-local rollback only; S3.9 remains the reusable
            # multi-record transaction layer.
            try:
                if old_fact is None:
                    fact_path.unlink(missing_ok=True)
                else:
                    _atomic_replace(fact_path, old_fact)
                if old_hash is None:
                    hash_path.unlink(missing_ok=True)
                else:
                    _atomic_replace(hash_path, old_hash)
            except Exception:
                pass
            raise

        return fact_set

    def open(self, project_id: CanonicalId | str) -> FactSet:
        handle = self._open_handle(project_id)
        fact_path, hash_path = self._paths(handle)
        raw = _read_bounded(
            fact_path,
            limit=MAX_FACT_STORE_BYTES,
            label="fact set",
        )
        expected = _read_hash(hash_path)
        actual = _hash_bytes(raw)
        if actual != expected:
            raise FactStoreConflictError("fact-set hash mismatch")

        payload = _load_json_object(raw, label="fact set")
        if frozenset(payload.keys()) != _FACT_FILE_KEYS:
            raise FactStoreConflictError(
                "fact-set file keys do not match S3.6 contract"
            )
        if payload["format_version"] != FACT_FORMAT_VERSION:
            raise FactStoreConflictError("unsupported fact-set format")
        if payload["project_id"] != handle.project_id.value:
            raise FactStoreConflictError("fact-set project ID mismatch")

        try:
            result = FactSet.from_dict(
                {
                    "project_id": payload["project_id"],
                    "facts": payload["facts"],
                }
            )
        except FactContractError as exc:
            raise FactStoreConflictError(
                f"stored FactSet is invalid: {exc}"
            ) from exc
        return result
