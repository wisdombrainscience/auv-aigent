#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts import ReviewIssue
from ai.project.store import ProjectStore
from ai.review import ReviewTaskGenerator, ReworkTaskGenerator
from ai.tasks.store import TaskCreateInput, TaskStore


def make_issue(issue_id: str, fingerprint: str) -> ReviewIssue:
    return ReviewIssue.from_dict({
        "issue_id": issue_id,
        "fingerprint": fingerprint,
        "severity": "major",
        "category": "engineering",
        "field": "mass_kg",
        "reason": "Review requires targeted rework",
        "required_action": "Return the finding to the artifact producer",
    })


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r9_5_acceptance_") as td:
        projects = ProjectStore(Path(td) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        producer = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Produce R9.5 review target",
                role="uav_systems_engineer",
                priority=7,
                dependencies=(),
                input_artifacts=(),
                output_schema="engineering_task.v1",
                validators=("schema",),
                review_required=True,
                max_attempts=3,
            ),
        )
        artifact = ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="ai.agent_result.v1",
                producer_task=producer.task_id,
                input_dependency_versions=(),
                content=b'{"acceptance":"r9.5"}\n',
                created_timestamp="2026-10-06T00:00:00Z",
            ),
        )
        review_task = ReviewTaskGenerator(projects).generate(
            handle.project_id, artifact.artifact_id, artifact.version
        ).task
        finding = make_issue("ISSUE-0001", "r9.5-acceptance-root")

        first = ReworkTaskGenerator(projects).generate(
            handle.project_id, review_task.task_id, finding
        )
        restarted = ProjectStore(projects.projects_root)
        second = ReworkTaskGenerator(restarted).generate(
            handle.project_id, review_task.task_id, finding
        )
        duplicate_fingerprint = ReworkTaskGenerator(restarted).generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0002", "r9.5-acceptance-root"),
        )

        task = first.task
        assert first.reused_existing is False
        assert second.reused_existing is True
        assert second.task.task_id == task.task_id
        assert duplicate_fingerprint.reused_existing is True
        assert duplicate_fingerprint.suppressed_duplicate is True
        assert duplicate_fingerprint.task.task_id == task.task_id
        assert task.role == producer.role == "uav_systems_engineer"
        assert task.dependencies == (review_task.task_id,)
        assert len(task.input_artifacts) == 1
        assert task.input_artifacts[0].entity_id == artifact.artifact_id
        assert task.input_artifacts[0].version == artifact.version
        assert task.output_schema == producer.output_schema
        assert task.validators == producer.validators
        assert task.review_required is True
        assert task.max_attempts == producer.max_attempts == 3
        assert task.status.value == "CREATED"
        assert task.attempt == 0
        assert len(tasks.list_tasks(handle.project_id)) == 3

    print(
        "REWORK_TASK_GENERATION=PASS "
        "owner_source=R9_4 owner_role=uav_systems_engineer dependency=review_task "
        "input_artifact=exact_review_target output_schema=inherited validators=inherited "
        "review_required=true status=CREATED attempt=0 task_max_attempts=3 "
        "issue_binding=id_plus_fingerprint_digest idempotent=PASS restart=PASS "
        "duplicate_fingerprint_new_issue=suppressed issue_record_persistence=none "
        "max_rework=active_R9_6 duplicate_suppression=active_R9_7 "
        "provider_calls=none task_execution=none cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
