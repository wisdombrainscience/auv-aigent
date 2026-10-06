from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ai.artifacts.schema import ArtifactInputVersion, ArtifactSchemaError
from ai.artifacts.supersession import (
    ArtifactSupersessionConflictError,
    ArtifactSupersessionNotFoundError,
    ArtifactSupersessionStore,
)
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import IdKind
from ai.project.facts import FactSetNotFoundError, FactStore, FactStoreConflictError
from ai.project.requirement_versions import (
    RequirementVersionConflictError,
    RequirementVersionNotFoundError,
    RequirementVersionStore,
)
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectStore
from ai.uav_bridge.proposal import (
    MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS,
    ExecutionScope,
    SimulationProposal,
    SimulationProposalError,
)


PROJECT_MISSION_MAPPING_CONTRACT_VERSION = "1.0"
PROJECT_MISSION_REQUEST_FACT_KEY = "simulation.mission_request"
MAPPED_MISSION_ID = "MISSION-001"
MAPPED_MISSION_FRAME = "LOCAL_NED"
MAX_PROJECT_MISSION_WAYPOINTS = 20

_MISSION_REQUEST_KEYS = frozenset(
    {
        "scenario_id",
        "home_position_m",
        "waypoints",
        "takeoff_altitude_m",
        "return_altitude_m",
        "position_tolerance_m",
        "altitude_tolerance_m",
        "horizontal_tolerance_m",
        "ground_altitude_tolerance_m",
        "continuity_tolerance_m",
        "terminal_home_tolerance_m",
        "timeout_s",
    }
)
_WAYPOINT_KEYS = frozenset({"north", "east", "altitude"})


class ProjectMissionMappingError(RuntimeError):
    """Raised when U11.2 cannot map approved Project State deterministically."""


def _finite(raw: Any, *, field: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ProjectMissionMappingError(f"{field} must be numeric")
    value = float(raw)
    if not math.isfinite(value):
        raise ProjectMissionMappingError(f"{field} must be finite")
    return value


def _positive(raw: Any, *, field: str) -> float:
    value = _finite(raw, field=field)
    if value <= 0:
        raise ProjectMissionMappingError(f"{field} must be > 0")
    return value


def _pair(raw: Any, *, field: str) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ProjectMissionMappingError(f"{field} must contain exactly two numbers")
    return [
        _finite(raw[0], field=f"{field}[0]"),
        _finite(raw[1], field=f"{field}[1]"),
    ]


def _mission_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ProjectMissionMappingError(
            f"{PROJECT_MISSION_REQUEST_FACT_KEY} must be a JSON object"
        )
    keys = frozenset(raw.keys())
    if keys != _MISSION_REQUEST_KEYS:
        missing = sorted(_MISSION_REQUEST_KEYS - keys)
        extra = sorted(keys - _MISSION_REQUEST_KEYS)
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(str(item) for item in extra))
        raise ProjectMissionMappingError(
            "mission request must contain exactly the U11.2 fields"
            + (" (" + "; ".join(detail) + ")" if detail else "")
        )

    scenario_id = raw["scenario_id"]
    if not isinstance(scenario_id, str):
        raise ProjectMissionMappingError("scenario_id must be a string")

    home = _pair(raw["home_position_m"], field="home_position_m")

    waypoints_raw = raw["waypoints"]
    if not isinstance(waypoints_raw, (list, tuple)):
        raise ProjectMissionMappingError("waypoints must be an array")
    if not 1 <= len(waypoints_raw) <= MAX_PROJECT_MISSION_WAYPOINTS:
        raise ProjectMissionMappingError(
            f"waypoints must contain 1..{MAX_PROJECT_MISSION_WAYPOINTS} entries"
        )
    waypoints: list[dict[str, float]] = []
    for index, item in enumerate(waypoints_raw):
        if not isinstance(item, Mapping) or frozenset(item.keys()) != _WAYPOINT_KEYS:
            raise ProjectMissionMappingError(
                f"waypoints[{index}] must contain exactly north,east,altitude"
            )
        altitude = _positive(item["altitude"], field=f"waypoints[{index}].altitude")
        waypoints.append(
            {
                "north": _finite(item["north"], field=f"waypoints[{index}].north"),
                "east": _finite(item["east"], field=f"waypoints[{index}].east"),
                "altitude": altitude,
            }
        )

    result = {
        "scenario_id": scenario_id,
        "home_position_m": home,
        "waypoints": waypoints,
    }
    for field in (
        "takeoff_altitude_m",
        "return_altitude_m",
        "position_tolerance_m",
        "altitude_tolerance_m",
        "horizontal_tolerance_m",
        "ground_altitude_tolerance_m",
        "continuity_tolerance_m",
        "terminal_home_tolerance_m",
        "timeout_s",
    ):
        result[field] = _positive(raw[field], field=field)
    return result


def _source_artifacts(raw: Iterable[ArtifactInputVersion | Mapping[str, Any]]) -> tuple[ArtifactInputVersion, ...]:
    try:
        materialized = tuple(raw)
    except TypeError as exc:
        raise ProjectMissionMappingError("source_artifacts must be iterable") from exc
    if not 1 <= len(materialized) <= MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS:
        raise ProjectMissionMappingError(
            "source_artifacts must contain 1.."
            f"{MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS} entries"
        )

    parsed: list[ArtifactInputVersion] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(materialized):
        try:
            ref = item if isinstance(item, ArtifactInputVersion) else ArtifactInputVersion.from_dict(item)
        except (ArtifactSchemaError, TypeError) as exc:
            raise ProjectMissionMappingError(f"source_artifacts[{index}] is invalid") from exc
        if ref.entity_id.kind is not IdKind.ARTIFACT:
            raise ProjectMissionMappingError(
                f"source_artifacts[{index}] must reference an ARTIFACT ID"
            )
        key = (ref.entity_id.value, ref.version)
        if key in seen:
            raise ProjectMissionMappingError("source_artifacts contains a duplicate artifact version")
        seen.add(key)
        parsed.append(ref)

    return tuple(sorted(parsed, key=lambda item: (item.entity_id.value, item.version)))


def _approved_requirement_version(projects: ProjectStore, project_id: Any) -> int:
    try:
        latest = RequirementVersionStore(projects).open_latest(project_id)
    except (RequirementVersionNotFoundError, RequirementVersionConflictError) as exc:
        raise ProjectMissionMappingError(f"requirement state is unavailable: {exc}") from exc
    if latest.requirement_set.status is not RequirementSetStatus.APPROVED:
        raise ProjectMissionMappingError("latest requirement version must be APPROVED")
    return latest.version


def _project_mission_request(projects: ProjectStore, project_id: Any) -> dict[str, Any]:
    try:
        fact_set = FactStore(projects).open(project_id)
    except (FactSetNotFoundError, FactStoreConflictError) as exc:
        raise ProjectMissionMappingError(f"project facts are unavailable: {exc}") from exc

    matching = [fact for fact in fact_set.facts if fact.key == PROJECT_MISSION_REQUEST_FACT_KEY]
    if len(matching) != 1:
        raise ProjectMissionMappingError(
            f"Project State must contain exactly one {PROJECT_MISSION_REQUEST_FACT_KEY} fact"
        )
    return _mission_request(matching[0].value)


def _require_approved_current_artifacts(
    projects: ProjectStore,
    project_id: Any,
    refs: tuple[ArtifactInputVersion, ...],
) -> None:
    versions = ArtifactVersionStore(projects)
    supersession = ArtifactSupersessionStore(projects)

    for ref in refs:
        try:
            record = versions.open_version(project_id, ref.entity_id, ref.version)
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise ProjectMissionMappingError(
                f"source artifact is unavailable: {ref.entity_id.value}@v{ref.version}"
            ) from exc
        if record.status is not ArtifactState.APPROVED:
            raise ProjectMissionMappingError(
                f"source artifact must be APPROVED: {ref.entity_id.value}@v{ref.version}"
            )
        try:
            current = supersession.current_approved(project_id, ref.entity_id)
        except (ArtifactSupersessionConflictError, ArtifactSupersessionNotFoundError) as exc:
            raise ProjectMissionMappingError(
                f"source artifact currentness could not be established: {ref.entity_id.value}"
            ) from exc
        if current is None or current.version != ref.version:
            raise ProjectMissionMappingError(
                f"source artifact is not the current APPROVED version: "
                f"{ref.entity_id.value}@v{ref.version}"
            )


def _build_mission_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    home_n, home_e = request["home_position_m"]
    takeoff_altitude = request["takeoff_altitude_m"]
    return_altitude = request["return_altitude_m"]
    timeout_s = request["timeout_s"]
    altitude_tolerance = request["altitude_tolerance_m"]
    horizontal_tolerance = request["horizontal_tolerance_m"]

    steps: list[dict[str, Any]] = [
        {
            "step_id": "takeoff",
            "capability_id": "TAKEOFF-001",
            "post_action": "hold",
            "input": {
                "task_id": "TAKEOFF-001",
                "frame": MAPPED_MISSION_FRAME,
                "target_altitude_m": takeoff_altitude,
                "altitude_tolerance_m": altitude_tolerance,
                "horizontal_tolerance_m": horizontal_tolerance,
                "timeout_s": timeout_s,
            },
        }
    ]

    for index, waypoint in enumerate(request["waypoints"], start=1):
        steps.append(
            {
                "step_id": f"waypoint_{index:02d}",
                "capability_id": "NAV-001",
                "post_action": "hold",
                "input": {
                    "task_id": "NAV-001",
                    "frame": MAPPED_MISSION_FRAME,
                    "target_position_m": [
                        waypoint["north"],
                        waypoint["east"],
                        -waypoint["altitude"],
                    ],
                    "position_tolerance_m": request["position_tolerance_m"],
                    "timeout_s": timeout_s,
                },
            }
        )

    steps.extend(
        [
            {
                "step_id": "return_home",
                "capability_id": "RETURN-HOME-001",
                "post_action": "hold",
                "input": {
                    "task_id": "RETURN-HOME-001",
                    "frame": MAPPED_MISSION_FRAME,
                    "home_position_m": [home_n, home_e],
                    "return_altitude_m": return_altitude,
                    "horizontal_tolerance_m": request["position_tolerance_m"],
                    "altitude_tolerance_m": altitude_tolerance,
                    "timeout_s": timeout_s,
                },
            },
            {
                "step_id": "land",
                "capability_id": "LAND-001",
                "post_action": "none",
                "input": {
                    "task_id": "LAND-001",
                    "frame": MAPPED_MISSION_FRAME,
                    "ground_altitude_tolerance_m": request["ground_altitude_tolerance_m"],
                    "horizontal_tolerance_m": horizontal_tolerance,
                    "timeout_s": timeout_s,
                },
            },
        ]
    )

    return {
        "mission_id": MAPPED_MISSION_ID,
        "frame": MAPPED_MISSION_FRAME,
        "continuity_tolerance_m": request["continuity_tolerance_m"],
        "terminal_home_position_m": [home_n, home_e],
        "terminal_home_tolerance_m": request["terminal_home_tolerance_m"],
        "steps": steps,
    }


def map_project_to_mission(
    project_store: ProjectStore,
    project_id: Any,
    source_artifacts: Iterable[ArtifactInputVersion | Mapping[str, Any]],
) -> SimulationProposal:
    """Map current approved Project State into one immutable U11.1 proposal.

    U11.2 reads only existing Project State and artifact registries. It does not
    persist the proposal, authorize capabilities/missions, enforce simulation
    region/runtime policy, invoke the runtime, access PX4/MAVSDK, or own a
    simulation verdict. Those responsibilities remain U11.3-U11.7 and the
    existing independent verifier.
    """

    if not isinstance(project_store, ProjectStore):
        raise TypeError("project_store must be ProjectStore")

    requirement_version = _approved_requirement_version(project_store, project_id)
    request = _project_mission_request(project_store, project_id)
    refs = _source_artifacts(source_artifacts)
    _require_approved_current_artifacts(project_store, project_id, refs)

    try:
        return SimulationProposal(
            project_id=project_id,
            requirement_version=requirement_version,
            source_artifacts=refs,
            scenario_id=request["scenario_id"],
            execution_scope=ExecutionScope.SIMULATION_ONLY,
            mission_id=MAPPED_MISSION_ID,
            mission_payload=_build_mission_payload(request),
        )
    except SimulationProposalError as exc:
        raise ProjectMissionMappingError(f"mapped proposal violates U11.1: {exc}") from exc
