from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from ai.artifacts.schema import ArtifactInputVersion, ArtifactSchemaError
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.requirement_versions import MAX_REQUIREMENT_VERSION


SIMULATION_PROPOSAL_CONTRACT_VERSION = "1.0"

MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS = 64
MAX_SIMULATION_PROPOSAL_SCENARIO_BYTES = 128
MAX_SIMULATION_PROPOSAL_MISSION_ID_CHARS = 64
MAX_SIMULATION_PROPOSAL_JSON_DEPTH = 12
MAX_SIMULATION_PROPOSAL_JSON_NODES = 4096
MAX_SIMULATION_PROPOSAL_JSON_BYTES = 256 * 1024

_MISSION_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,63}$")
_PROPOSAL_KEYS = frozenset(
    {
        "project_id",
        "requirement_version",
        "source_artifacts",
        "scenario_id",
        "execution_scope",
        "mission_id",
        "mission_payload",
    }
)


class SimulationProposalError(ValueError):
    """Raised when a U11.1 simulation proposal violates its wire contract."""


class ExecutionScope(str, Enum):
    """Execution scopes accepted by the frozen Remote UAV V8 architecture."""

    SIMULATION_ONLY = "SIMULATION_ONLY"


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]


def _project_id(raw: Any) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise SimulationProposalError("project_id must be a canonical PROJECT ID") from exc
    if value.kind is not IdKind.PROJECT:
        raise SimulationProposalError("project_id must be a canonical PROJECT ID")
    return value


def _requirement_version(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SimulationProposalError("requirement_version must be an integer")
    if not 1 <= raw <= MAX_REQUIREMENT_VERSION:
        raise SimulationProposalError("requirement_version is outside the supported range")
    return raw


def _scenario_id(raw: Any) -> str:
    if not isinstance(raw, str):
        raise SimulationProposalError("scenario_id must be a string")
    if not raw or raw != raw.strip() or "\x00" in raw:
        raise SimulationProposalError("scenario_id must be clean non-empty text")
    if len(raw.encode("utf-8")) > MAX_SIMULATION_PROPOSAL_SCENARIO_BYTES:
        raise SimulationProposalError("scenario_id exceeds its byte limit")
    return raw


def _execution_scope(raw: Any) -> ExecutionScope:
    if isinstance(raw, ExecutionScope):
        scope = raw
    else:
        try:
            scope = ExecutionScope(raw)
        except (TypeError, ValueError) as exc:
            raise SimulationProposalError(
                "execution_scope must be exactly SIMULATION_ONLY"
            ) from exc
    if scope is not ExecutionScope.SIMULATION_ONLY:
        raise SimulationProposalError("only SIMULATION_ONLY is permitted")
    return scope


def _mission_id(raw: Any) -> str:
    if not isinstance(raw, str):
        raise SimulationProposalError("mission_id must be a string")
    if len(raw) > MAX_SIMULATION_PROPOSAL_MISSION_ID_CHARS or _MISSION_ID_RE.fullmatch(raw) is None:
        raise SimulationProposalError("mission_id must match [A-Z][A-Z0-9-]{0,63}")
    return raw


def _source_artifacts(raw: Any) -> tuple[ArtifactInputVersion, ...]:
    if not isinstance(raw, (list, tuple)):
        raise SimulationProposalError("source_artifacts must be an array")
    if not 1 <= len(raw) <= MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS:
        raise SimulationProposalError(
            "source_artifacts must contain 1.."
            f"{MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS} entries"
        )

    result: list[ArtifactInputVersion] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(raw):
        try:
            ref = item if isinstance(item, ArtifactInputVersion) else ArtifactInputVersion.from_dict(item)
        except (ArtifactSchemaError, TypeError) as exc:
            raise SimulationProposalError(
                f"source_artifacts[{index}] is invalid"
            ) from exc
        if ref.entity_id.kind is not IdKind.ARTIFACT:
            raise SimulationProposalError(
                f"source_artifacts[{index}] must reference an ARTIFACT ID"
            )
        key = (ref.entity_id.value, ref.version)
        if key in seen:
            raise SimulationProposalError("source_artifacts contains a duplicate artifact version")
        seen.add(key)
        result.append(ref)
    return tuple(result)


def _freeze_json(value: Any, *, path: str, depth: int, counter: list[int]) -> JSONValue:
    if depth > MAX_SIMULATION_PROPOSAL_JSON_DEPTH:
        raise SimulationProposalError("mission_payload exceeds maximum JSON depth")
    counter[0] += 1
    if counter[0] > MAX_SIMULATION_PROPOSAL_JSON_NODES:
        raise SimulationProposalError("mission_payload exceeds maximum JSON node count")

    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and "\x00" in value:
            raise SimulationProposalError(f"{path} contains NUL text")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SimulationProposalError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", depth=depth + 1, counter=counter)
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise SimulationProposalError(f"{path} contains an invalid object key")
            frozen[key] = _freeze_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
                counter=counter,
            )
        return MappingProxyType(frozen)
    raise SimulationProposalError(
        f"{path} contains unsupported JSON value type {type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def _mission_payload(raw: Any) -> Mapping[str, JSONValue]:
    if not isinstance(raw, Mapping):
        raise SimulationProposalError("mission_payload must be a JSON object")
    frozen = _freeze_json(raw, path="mission_payload", depth=0, counter=[0])
    if not isinstance(frozen, Mapping):
        raise SimulationProposalError("mission_payload must be a JSON object")
    serialized = json.dumps(
        _thaw_json(frozen),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > MAX_SIMULATION_PROPOSAL_JSON_BYTES:
        raise SimulationProposalError("mission_payload exceeds maximum serialized size")
    return frozen


@dataclass(frozen=True)
class SimulationProposal:
    """Immutable U11.1 proposal crossing toward the future UAV Bridge.

    U11.1 defines data shape only. It does not read Project State, decide whether
    source artifacts are approved/current, map engineering state to MISSION-001,
    validate bridge policy, authorize a capability/mission, invoke the existing
    UAV runtime, persist a UAV run, or own a simulation verdict. Those behaviors
    belong to U11.2-U11.7 and the existing independent verifier.
    """

    project_id: CanonicalId
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    scenario_id: str
    execution_scope: ExecutionScope
    mission_id: str
    mission_payload: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _project_id(self.project_id))
        object.__setattr__(self, "requirement_version", _requirement_version(self.requirement_version))
        object.__setattr__(self, "source_artifacts", _source_artifacts(self.source_artifacts))
        object.__setattr__(self, "scenario_id", _scenario_id(self.scenario_id))
        object.__setattr__(self, "execution_scope", _execution_scope(self.execution_scope))
        object.__setattr__(self, "mission_id", _mission_id(self.mission_id))
        object.__setattr__(self, "mission_payload", _mission_payload(self.mission_payload))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SimulationProposal":
        if not isinstance(raw, Mapping):
            raise SimulationProposalError("simulation proposal payload must be an object")
        keys = frozenset(raw.keys())
        if keys != _PROPOSAL_KEYS:
            missing = sorted(_PROPOSAL_KEYS - keys)
            extra = sorted(keys - _PROPOSAL_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise SimulationProposalError(
                "simulation proposal payload must contain exactly the U11.1 fields"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )
        return cls(
            project_id=raw["project_id"],
            requirement_version=raw["requirement_version"],
            source_artifacts=raw["source_artifacts"],
            scenario_id=raw["scenario_id"],
            execution_scope=raw["execution_scope"],
            mission_id=raw["mission_id"],
            mission_payload=raw["mission_payload"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "scenario_id": self.scenario_id,
            "execution_scope": self.execution_scope.value,
            "mission_id": self.mission_id,
            "mission_payload": _thaw_json(self.mission_payload),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def parse_simulation_proposal(raw: Mapping[str, Any]) -> SimulationProposal:
    return SimulationProposal.from_dict(raw)
