from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleRegistry, RoleRegistryError
from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.simulation_feedback.taxonomy import (
    MAX_SIMULATION_ISSUES,
    SimulationIssue,
    SimulationIssueSet,
    SimulationIssueType,
)


SIMULATION_ISSUE_ROLE_MAPPING_CONTRACT_VERSION = "1.0"
UAV_SYSTEMS_ENGINEER_ROLE_ID = "uav_systems_engineer"


class SimulationIssueRoleMappingError(ValueError):
    """Raised when F12.3 cannot deterministically route a simulation issue."""


# The frozen MVP activates only coordinator, requirements_engineer,
# uav_systems_engineer, and reviewer. F12.3 must not silently activate the
# reserved simulation/verification specialists, so every simulation-feedback
# root cause currently routes to the active engineering owner.
_SIMULATION_ISSUE_ROLE_MAP: Mapping[SimulationIssueType, str] = MappingProxyType(
    {
        issue_type: UAV_SYSTEMS_ENGINEER_ROLE_ID
        for issue_type in SimulationIssueType
    }
)


@dataclass(frozen=True)
class RoutedSimulationIssue:
    """One immutable F12.3 route for one already-classified F12.2 issue."""

    issue: SimulationIssue
    owner_role: str

    def __post_init__(self) -> None:
        if not isinstance(self.issue, SimulationIssue):
            raise SimulationIssueRoleMappingError("issue must be SimulationIssue")
        expected = _SIMULATION_ISSUE_ROLE_MAP[self.issue.issue_type]
        if self.owner_role != expected:
            raise SimulationIssueRoleMappingError(
                "owner_role does not match deterministic simulation issue mapping"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue.to_dict(),
            "owner_role": self.owner_role,
        }


@dataclass(frozen=True)
class SimulationIssueRolePlan:
    """Immutable F12.3 envelope preserving verifier/project lineage.

    F12.3 is routing only. It allocates no ISSUE ID, persists no record,
    invalidates no artifact, creates no task, grants no retry permission, and
    performs no provider or UAV runtime call.
    """

    project_id: CanonicalId
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    runtime_run_id: str
    mission_id: str
    scenario_id: str
    verification_sha256: str
    routes: tuple[RoutedSimulationIssue, ...]
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise SimulationIssueRoleMappingError("project_id must be a canonical PROJECT ID")
        if isinstance(self.requirement_version, bool) or not isinstance(self.requirement_version, int) or self.requirement_version <= 0:
            raise SimulationIssueRoleMappingError("requirement_version must be a positive integer")
        if not isinstance(self.source_artifacts, tuple) or any(
            not isinstance(item, ArtifactInputVersion) for item in self.source_artifacts
        ):
            raise SimulationIssueRoleMappingError("source_artifacts must preserve artifact lineage")
        for field, value in (
            ("runtime_run_id", self.runtime_run_id),
            ("mission_id", self.mission_id),
            ("scenario_id", self.scenario_id),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise SimulationIssueRoleMappingError(f"{field} must be clean non-empty text")
        if not isinstance(self.verification_sha256, str) or len(self.verification_sha256) != 64:
            raise SimulationIssueRoleMappingError("verification_sha256 must be lowercase sha256")
        if any(ch not in "0123456789abcdef" for ch in self.verification_sha256):
            raise SimulationIssueRoleMappingError("verification_sha256 must be lowercase sha256")
        if not isinstance(self.routes, tuple):
            raise SimulationIssueRoleMappingError("routes must be a tuple")
        if len(self.routes) > MAX_SIMULATION_ISSUES:
            raise SimulationIssueRoleMappingError("routes exceeds bounded size")
        if any(not isinstance(route, RoutedSimulationIssue) for route in self.routes):
            raise SimulationIssueRoleMappingError("routes contains invalid item")
        fingerprints = tuple(route.issue.fingerprint for route in self.routes)
        if len(set(fingerprints)) != len(fingerprints):
            raise SimulationIssueRoleMappingError("routes contains duplicate issue fingerprint")
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationIssueRoleMappingError("provenance must remain VERIFIER_RESULT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": SIMULATION_ISSUE_ROLE_MAPPING_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "runtime_run_id": self.runtime_run_id,
            "mission_id": self.mission_id,
            "scenario_id": self.scenario_id,
            "verification_sha256": self.verification_sha256,
            "routes": [route.to_dict() for route in self.routes],
            "provenance": self.provenance.value,
        }


def issue_role_map() -> Mapping[SimulationIssueType, str]:
    """Return the immutable explicit F12.3 issue-to-role policy."""
    return _SIMULATION_ISSUE_ROLE_MAP


def route_simulation_issues(
    issue_set: SimulationIssueSet,
    *,
    role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
) -> SimulationIssueRolePlan:
    """Map F12.2 issues to an active engineering role without side effects."""

    if not isinstance(issue_set, SimulationIssueSet):
        raise TypeError("issue_set must be SimulationIssueSet")
    if not isinstance(role_registry, RoleRegistry):
        raise TypeError("role_registry must be RoleRegistry")

    routes: list[RoutedSimulationIssue] = []
    for issue in issue_set.issues:
        owner_role = _SIMULATION_ISSUE_ROLE_MAP[issue.issue_type]
        try:
            canonical_role = role_registry.require_known(owner_role)
        except RoleRegistryError as exc:
            raise SimulationIssueRoleMappingError(
                f"mapped engineering role is not active: {owner_role}"
            ) from exc
        routes.append(RoutedSimulationIssue(issue=issue, owner_role=canonical_role))

    return SimulationIssueRolePlan(
        project_id=issue_set.project_id,
        requirement_version=issue_set.requirement_version,
        source_artifacts=issue_set.source_artifacts,
        runtime_run_id=issue_set.runtime_run_id,
        mission_id=issue_set.mission_id,
        scenario_id=issue_set.scenario_id,
        verification_sha256=issue_set.verification_sha256,
        routes=tuple(routes),
    )
