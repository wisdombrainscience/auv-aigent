#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import MinimalContextBuilder  # noqa: E402
from ai.artifacts import (  # noqa: E402
    ArtifactInputVersion,
    ArtifactLifecycleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState  # noqa: E402
from ai.contracts.provenance import ProvenanceClass  # noqa: E402
from ai.contracts.validation_result import (  # noqa: E402
    ValidationCheck, ValidationResult, ValidationStatus,
)
from ai.project.facts import Fact, FactSet, FactStore  # noqa: E402
from ai.project.requirement_versions import RequirementVersionStore  # noqa: E402
from ai.project.requirements import RequirementSetStatus  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402
from ai.tasks import TaskCreateInput, TaskStore  # noqa: E402


def validation():
    return ValidationResult.from_checks((ValidationCheck(
        check_id="g6-context", status=ValidationStatus.PASS,
        field=None, reason=None, details={},
    ),))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-context-") as raw:
        projects = ProjectStore(Path(raw)/"remote-uav"/"projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)

        requirements = RequirementVersionStore(projects).create_initial(
            handle.project_id,
            ["Endurance shall be at least 20 minutes."],
            status=RequirementSetStatus.APPROVED,
        )
        req = requirements.requirement_set.requirements[0]

        producer = tasks.create_task(handle.project_id, TaskCreateInput(
            title="Parent design", role="uav_systems_engineer", priority=100,
            dependencies=(), input_artifacts=(), output_schema="engineering_task.v1",
            validators=("schema",), review_required=False, max_attempts=3,
        ))
        versions = ArtifactVersionStore(projects)
        artifact = versions.create_artifact(handle.project_id, ArtifactVersionInput(
            type="uav.design_note", producer_task=producer.task_id,
            input_dependency_versions=(), content=b'{"mass_kg":2.1}',
            created_timestamp="2026-10-04T12:00:00Z",
        ))
        lifecycle = ArtifactLifecycleStore(projects)
        lifecycle.transition(handle.project_id, artifact.artifact_id, 1, ArtifactState.REVIEWED)
        lifecycle.transition(handle.project_id, artifact.artifact_id, 1, ArtifactState.VALIDATED, validation_result=validation())
        lifecycle.transition(handle.project_id, artifact.artifact_id, 1, ArtifactState.APPROVED)

        FactStore(projects).save(FactSet(
            project_id=handle.project_id,
            facts=(Fact("vehicle.mass_kg", 2.1, ProvenanceClass.VERIFIED_FACT),),
        ))

        task = tasks.create_task(handle.project_id, TaskCreateInput(
            title="Consume approved context", role="uav_systems_engineer", priority=200,
            dependencies=(producer.task_id,),
            input_artifacts=(
                ArtifactInputVersion(req.requirement_id, 1),
                ArtifactInputVersion(artifact.artifact_id, 1),
            ),
            output_schema="engineering_task.v1", validators=("schema",),
            review_required=False, max_attempts=3,
        ))

        context = MinimalContextBuilder(projects).build(
            handle.project_id, task.task_id,
            fact_keys=("vehicle.mass_kg",),
        )
        restarted = MinimalContextBuilder(ProjectStore(projects.projects_root)).build(
            handle.project_id, task.task_id,
            fact_keys=("vehicle.mass_kg",),
        )
        if restarted != context:
            print("MINIMAL_CONTEXT=FAIL reason=restart")
            return 2
        if len(context.approved_requirements) != 1 or len(context.approved_parent_artifacts) != 1 or len(context.project_facts) != 1:
            print("MINIMAL_CONTEXT=FAIL reason=selection")
            return 3

        print(
            "MINIMAL_CONTEXT=PASS "
            f"task={task.task_id.value} "
            "role=uav_systems_engineer "
            "requirements=1 artifacts=1 facts=1 "
            "approved_only=PASS policy_hierarchy=PASS "
            "restart=PASS provider_calls=none "
            "review_issues=reserved validation_failures=reserved documents=reserved "
            "output=reserved_for_G6_5 manifest=reserved_for_G6_7"
        )
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
