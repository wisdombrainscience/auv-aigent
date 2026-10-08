from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai.artifacts.lineage import ArtifactVersionRef
from ai.artifacts.schema import ArtifactInputVersion
from ai.artifacts.staleness import (
    ArtifactStaleConflictError,
    ArtifactStaleError,
    ArtifactStaleNotFoundError,
    ArtifactStaleStore,
)
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.ids import IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.project.store import ProjectStore
from ai.simulation_feedback.routing import SimulationIssueRolePlan
from ai.uav_bridge.proposal import MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS


SIMULATION_LINEAGE_INVALIDATION_CONTRACT_VERSION = "1.0"
MAX_SIMULATION_INVALIDATION_ROOTS = MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS


class SimulationLineageInvalidationError(RuntimeError):
    """Raised when F12.4 cannot invalidate exact simulation source lineage safely."""


@dataclass(frozen=True)
class SimulationLineageInvalidationResult:
    """Deterministic F12.4 result for one routed simulation-feedback plan.

    The result records only existing artifact-lifecycle effects. It allocates no
    ISSUE ID, creates no rework task, grants no simulation retry permission, and
    performs no provider/UAV call.
    """

    project_id: Any
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    runtime_run_id: str
    mission_id: str
    scenario_id: str
    verification_sha256: str
    issue_fingerprints: tuple[str, ...]
    invalidation_roots: tuple[ArtifactInputVersion, ...]
    affected: tuple[ArtifactVersionRef, ...]
    changed_to_stale: tuple[ArtifactVersionRef, ...]
    already_historical: tuple[ArtifactVersionRef, ...]
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.source_artifacts, tuple) or any(
            not isinstance(item, ArtifactInputVersion) for item in self.source_artifacts
        ):
            raise SimulationLineageInvalidationError(
                "source_artifacts must preserve exact artifact lineage"
            )
        if not isinstance(self.invalidation_roots, tuple) or any(
            not isinstance(item, ArtifactInputVersion) for item in self.invalidation_roots
        ):
            raise SimulationLineageInvalidationError(
                "invalidation_roots must contain ArtifactInputVersion values"
            )
        if len(self.invalidation_roots) > MAX_SIMULATION_INVALIDATION_ROOTS:
            raise SimulationLineageInvalidationError(
                "invalidation_roots exceeds bounded size"
            )
        if any(item.entity_id.kind is not IdKind.ARTIFACT for item in self.invalidation_roots):
            raise SimulationLineageInvalidationError(
                "invalidation roots must identify artifact versions"
            )
        if len(
            {
                (item.entity_id.value, item.version)
                for item in self.invalidation_roots
            }
        ) != len(self.invalidation_roots):
            raise SimulationLineageInvalidationError(
                "invalidation_roots contains duplicates"
            )
        if not isinstance(self.issue_fingerprints, tuple):
            raise SimulationLineageInvalidationError(
                "issue_fingerprints must be a tuple"
            )
        if len(set(self.issue_fingerprints)) != len(self.issue_fingerprints):
            raise SimulationLineageInvalidationError(
                "issue_fingerprints contains duplicates"
            )
        for field_name in ("affected", "changed_to_stale", "already_historical"):
            value = getattr(self, field_name)
            if not isinstance(value, tuple) or any(
                not isinstance(item, ArtifactVersionRef) for item in value
            ):
                raise SimulationLineageInvalidationError(
                    f"{field_name} must contain ArtifactVersionRef values"
                )
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationLineageInvalidationError(
                "provenance must remain VERIFIER_RESULT"
            )

    def to_dict(self) -> dict[str, Any]:
        def refs(items: tuple[ArtifactVersionRef, ...]) -> list[dict[str, Any]]:
            return [
                {
                    "artifact_id": item.artifact_id.value,
                    "version": item.version,
                }
                for item in items
            ]

        return {
            "contract_version": SIMULATION_LINEAGE_INVALIDATION_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "runtime_run_id": self.runtime_run_id,
            "mission_id": self.mission_id,
            "scenario_id": self.scenario_id,
            "verification_sha256": self.verification_sha256,
            "issue_fingerprints": list(self.issue_fingerprints),
            "invalidation_roots": [item.to_dict() for item in self.invalidation_roots],
            "affected": refs(self.affected),
            "changed_to_stale": refs(self.changed_to_stale),
            "already_historical": refs(self.already_historical),
            "provenance": self.provenance.value,
        }


def _sorted_unique_refs(
    refs: list[ArtifactVersionRef],
) -> tuple[ArtifactVersionRef, ...]:
    by_key = {
        (ref.artifact_id.value, ref.version): ref
        for ref in refs
    }
    return tuple(
        by_key[key]
        for key in sorted(by_key)
    )


def _validate_roots(
    projects: ProjectStore,
    plan: SimulationIssueRolePlan,
) -> tuple[ArtifactInputVersion, ...]:
    roots = tuple(
        sorted(
            plan.source_artifacts,
            key=lambda item: (item.entity_id.value, item.version),
        )
    )
    if not roots:
        raise SimulationLineageInvalidationError(
            "simulation feedback plan has no source artifact lineage"
        )
    if len(roots) > MAX_SIMULATION_INVALIDATION_ROOTS:
        raise SimulationLineageInvalidationError(
            "source artifact lineage exceeds bounded invalidation roots"
        )
    if any(item.entity_id.kind is not IdKind.ARTIFACT for item in roots):
        raise SimulationLineageInvalidationError(
            "simulation lineage invalidation requires artifact source versions"
        )
    if len({(item.entity_id.value, item.version) for item in roots}) != len(roots):
        raise SimulationLineageInvalidationError(
            "simulation source artifact lineage contains duplicates"
        )

    # Validate every root before mutating any lifecycle state. This prevents a
    # missing/cross-project source later in the list from causing a partial
    # multi-root invalidation.
    versions = ArtifactVersionStore(projects)
    for root in roots:
        try:
            record = versions.open_version(
                plan.project_id,
                root.entity_id,
                root.version,
            )
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise SimulationLineageInvalidationError(
                "simulation source artifact is unavailable: "
                f"{root.entity_id.value}@v{root.version}"
            ) from exc
        if (
            record.artifact_id != root.entity_id
            or record.version != root.version
            or record.project_id != plan.project_id
        ):
            raise SimulationLineageInvalidationError(
                "simulation source artifact lineage does not match project"
            )
    return roots


def invalidate_simulation_lineage(
    projects: ProjectStore,
    plan: SimulationIssueRolePlan,
) -> SimulationLineageInvalidationResult:
    """Invalidate exact simulation source artifacts after verifier failure.

    A PASS result produces an F12.3 plan with no routes and therefore performs
    no state mutation. A FAIL plan invalidates each exact source artifact
    version and lets the existing A4.6 store propagate STALE transitively.

    F12.4 deliberately does not allocate ISSUE IDs, create rework tasks, retry
    simulation, or transition project/task state to NEEDS_HUMAN.
    """

    if not isinstance(projects, ProjectStore):
        raise TypeError("projects must be ProjectStore")
    if not isinstance(plan, SimulationIssueRolePlan):
        raise TypeError("plan must be SimulationIssueRolePlan")

    fingerprints = tuple(route.issue.fingerprint for route in plan.routes)

    if not plan.routes:
        return SimulationLineageInvalidationResult(
            project_id=plan.project_id,
            requirement_version=plan.requirement_version,
            source_artifacts=plan.source_artifacts,
            runtime_run_id=plan.runtime_run_id,
            mission_id=plan.mission_id,
            scenario_id=plan.scenario_id,
            verification_sha256=plan.verification_sha256,
            issue_fingerprints=(),
            invalidation_roots=(),
            affected=(),
            changed_to_stale=(),
            already_historical=(),
        )

    roots = _validate_roots(projects, plan)
    stale = ArtifactStaleStore(projects)

    affected: list[ArtifactVersionRef] = []
    changed: list[ArtifactVersionRef] = []
    historical: list[ArtifactVersionRef] = []

    for root in roots:
        try:
            result = stale.mark_artifact_stale(
                plan.project_id,
                root.entity_id,
                root.version,
            )
        except (ArtifactStaleError, ArtifactStaleConflictError, ArtifactStaleNotFoundError) as exc:
            raise SimulationLineageInvalidationError(
                "artifact STALE propagation failed"
            ) from exc
        affected.extend(result.affected)
        changed.extend(result.changed_to_stale)
        historical.extend(result.already_historical)

    return SimulationLineageInvalidationResult(
        project_id=plan.project_id,
        requirement_version=plan.requirement_version,
        source_artifacts=plan.source_artifacts,
        runtime_run_id=plan.runtime_run_id,
        mission_id=plan.mission_id,
        scenario_id=plan.scenario_id,
        verification_sha256=plan.verification_sha256,
        issue_fingerprints=fingerprints,
        invalidation_roots=roots,
        affected=_sorted_unique_refs(affected),
        changed_to_stale=_sorted_unique_refs(changed),
        already_historical=_sorted_unique_refs(historical),
    )
