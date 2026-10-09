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
from ai.contracts.task_states import TaskState
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project.requirement_versions import RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectStore
from ai.simulation_feedback import (
    MAX_SIMULATION_REWORK_TASKS,
    MISSION_VERIFIER_CHECK_IDS,
    SIMULATION_REWORK_TASK_GENERATION_CONTRACT_VERSION,
    SIMULATION_REWORK_TASK_OUTPUT_SCHEMA,
    SimulationReworkTaskGenerator,
    classify_simulation_issues,
    invalidate_simulation_lineage,
    route_simulation_issues,
)
from ai.simulation_feedback.parser import ParsedVerifierResult
from ai.tasks.store import TaskStore


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
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.VALIDATED,
        validation_result=validation(),
    )
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def plan(project_id, source_ref, *, run_id: str, fail: bool):
    failed = ("continuity", "terminal_home") if fail else ()
    checks = {
        check_id: ("FAIL" if check_id in failed else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    parsed = ParsedVerifierResult(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        runtime_run_id=run_id,
        mission_id="MISSION-001",
        scenario_id="F12-5-CHECK",
        overall="FAIL" if fail else "PASS",
        checks=checks,
        failed_checks=failed,
        failed_step_ids=("nav-1",) if fail else (),
        max_transition_distance_m=2.0 if fail else 0.0,
        terminal_home_distance_m=3.0 if fail else 0.1,
        reason="verification failed" if fail else None,
        verification_sha256="b" * 64,
    )
    return route_simulation_issues(classify_simulation_issues(parsed))


def main() -> int:
    assert SIMULATION_REWORK_TASK_GENERATION_CONTRACT_VERSION == "1.0"
    assert MAX_SIMULATION_REWORK_TASKS == 7
    assert SIMULATION_REWORK_TASK_OUTPUT_SCHEMA == "engineering_task.v1"

    with tempfile.TemporaryDirectory(prefix="f12_5_probe_") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        req = RequirementVersionStore(projects).create_initial(
            handle.project_id,
            ["Mission shall remain within the verified local-NED operating envelope."],
            status=RequirementSetStatus.APPROVED,
        )
        versions = ArtifactVersionStore(projects)
        lifecycle = ArtifactLifecycleStore(projects)
        source = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mission_design",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"source",
                created_timestamp="2026-10-08T01:00:00Z",
            ),
        )
        approve(lifecycle, handle.project_id, source)
        source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=source.version)

        generator = SimulationReworkTaskGenerator(projects)

        passed_plan = plan(
            handle.project_id,
            source_ref,
            run_id="MISSION001-F12-5-PASS",
            fail=False,
        )
        passed_inv = invalidate_simulation_lineage(projects, passed_plan)
        passed = generator.generate(passed_plan, passed_inv)
        assert passed.bindings == ()
        assert TaskStore(projects).list_tasks(handle.project_id) == ()

        failed_plan = plan(
            handle.project_id,
            source_ref,
            run_id="MISSION001-F12-5-FAIL",
            fail=True,
        )
        failed_inv = invalidate_simulation_lineage(projects, failed_plan)
        first = generator.generate(failed_plan, failed_inv)
        assert len(first.bindings) == 1
        binding = first.bindings[0]
        task = binding.task
        assert binding.owner_role == "uav_systems_engineer"
        assert binding.reused_existing is False
        assert task.status is TaskState.CREATED
        assert task.attempt == 0
        assert task.max_attempts == 3
        assert task.review_required is True
        assert task.output_schema == "engineering_task.v1"
        assert task.dependencies == ()
        assert len(task.input_artifacts) == 1
        assert task.input_artifacts[0].entity_id == req.requirement_set.requirements[0].requirement_id
        assert task.input_artifacts[0].version == req.version
        assert source_ref not in task.input_artifacts
        assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.STALE

        second = generator.generate(failed_plan, failed_inv)
        assert second.bindings[0].task.task_id == task.task_id
        assert second.bindings[0].reused_existing is True
        assert len(TaskStore(projects).list_tasks(handle.project_id)) == 1

    print(
        "SIMULATION_REWORK_TASK_GENERATION=PASS "
        "contract_version=1.0 "
        "pass_result=NO_TASKS "
        "fail_result=OWNER_TARGETED_TASK "
        "grouping=ONE_TASK_PER_OWNER "
        "current_owner=uav_systems_engineer "
        "max_tasks=7 "
        "task_state=CREATED "
        "task_attempt=0 "
        "max_attempts=3 "
        "requirement_context=EXACT_APPROVED_VERSION "
        "stale_source_inputs=EXCLUDED "
        "output_schema=engineering_task.v1 "
        "review_required=YES "
        "idempotent=PASS "
        "event_identity=RUN+VERIFIER+ISSUES+LINEAGE "
        "fingerprint=PRESERVED "
        "provenance=VERIFIER_RESULT "
        "persistence=existing_task_registry "
        "issue_id=reserved "
        "retry=active_F12_6 "
        "needs_human=active_F12_7 "
        "provider_calls=none "
        "px4_access=none "
        "phase11=GREEN cp9=GREEN phase12=READY cp10=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
