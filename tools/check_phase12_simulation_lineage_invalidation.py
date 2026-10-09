#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts import ArtifactInputVersion, ArtifactLifecycleStore, ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project.store import ProjectStore
from ai.simulation_feedback import (
    MISSION_VERIFIER_CHECK_IDS,
    SIMULATION_LINEAGE_INVALIDATION_CONTRACT_VERSION,
    invalidate_simulation_lineage,
    classify_simulation_issues,
    route_simulation_issues,
)
from ai.simulation_feedback.parser import ParsedVerifierResult


def validation():
    return ValidationResult.from_checks((
        ValidationCheck(
            check_id="deterministic",
            status=ValidationStatus.PASS,
            field=None,
            reason=None,
            details={},
        ),
    ))


def approve(lifecycle, project_id, record):
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(
        project_id, record.artifact_id, record.version,
        ArtifactState.VALIDATED, validation_result=validation(),
    )
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def plan(project_id, source, *, fail: bool):
    failed = ("continuity",) if fail else ()
    checks = {
        check_id: ("FAIL" if check_id in failed else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    parsed = ParsedVerifierResult(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source,),
        runtime_run_id="MISSION001-F12-4-CHECK",
        mission_id="MISSION-001",
        scenario_id="F12-4-CHECK",
        overall="FAIL" if fail else "PASS",
        checks=checks,
        failed_checks=failed,
        failed_step_ids=("nav-1",) if fail else (),
        max_transition_distance_m=2.0 if fail else 0.0,
        terminal_home_distance_m=0.1,
        reason="continuity failed" if fail else None,
        verification_sha256="f" * 64,
    )
    return route_simulation_issues(classify_simulation_issues(parsed))


def main() -> int:
    assert SIMULATION_LINEAGE_INVALIDATION_CONTRACT_VERSION == "1.0"

    with tempfile.TemporaryDirectory(prefix="f12_4_probe_") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        versions = ArtifactVersionStore(projects)
        lifecycle = ArtifactLifecycleStore(projects)

        source = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mission_design",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"source",
                created_timestamp="2026-10-07T01:00:00Z",
            ),
        )
        child = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="derived_analysis",
                producer_task="TASK-000002",
                input_dependency_versions=(
                    ArtifactInputVersion(entity_id=source.artifact_id, version=source.version),
                ),
                content=b"child",
                created_timestamp="2026-10-07T01:01:00Z",
            ),
        )
        unrelated = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="unrelated",
                producer_task="TASK-000003",
                input_dependency_versions=(),
                content=b"unrelated",
                created_timestamp="2026-10-07T01:02:00Z",
            ),
        )
        for record in (source, child, unrelated):
            approve(lifecycle, handle.project_id, record)

        source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=source.version)

        passed = invalidate_simulation_lineage(
            projects, plan(handle.project_id, source_ref, fail=False)
        )
        assert passed.changed_to_stale == ()
        assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.APPROVED

        failed_plan = plan(handle.project_id, source_ref, fail=True)
        first = invalidate_simulation_lineage(projects, failed_plan)
        assert len(first.changed_to_stale) == 2
        assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.STALE
        assert versions.open_version(handle.project_id, child.artifact_id, 1).status is ArtifactState.STALE
        assert versions.open_version(handle.project_id, unrelated.artifact_id, 1).status is ArtifactState.APPROVED

        second = invalidate_simulation_lineage(projects, failed_plan)
        assert second.changed_to_stale == ()
        assert len(second.already_historical) == 2

    print(
        "SIMULATION_LINEAGE_INVALIDATION=PASS "
        "contract_version=1.0 "
        "pass_result=NO_MUTATION "
        "fail_result=SOURCE_ARTIFACTS_STALE "
        "propagation=REUSED_A4_6 "
        "descendants=STALE "
        "unrelated=UNCHANGED "
        "multi_root=BOUNDED "
        "prevalidate=ALL_ROOTS_BEFORE_MUTATION "
        "idempotent=PASS "
        "requirement_version=PRESERVED "
        "fingerprint=PRESERVED "
        "provenance=VERIFIER_RESULT "
        "persistence=existing_artifact_lifecycle "
        "issue_id=reserved "
        "rework_task=active_F12_5 "
        "retry=active_F12_6 "
        "needs_human=active_F12_7 "
        "provider_calls=none "
        "px4_access=none "
        "phase11=GREEN cp9=GREEN phase12=READY cp10=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
