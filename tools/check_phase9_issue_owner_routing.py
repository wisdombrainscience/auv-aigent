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
from ai.review import IssueOwnerRouter, ReviewTaskGenerator
from ai.tasks.store import TaskCreateInput, TaskStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r9_4_acceptance_") as td:
        projects = ProjectStore(Path(td) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        producer = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Produce routed review target",
                role="uav_systems_engineer",
                priority=5,
                dependencies=(),
                input_artifacts=(),
                output_schema="engineering_task.v1",
                validators=(),
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
                content=b'{"acceptance":"r9.4"}\n',
                created_timestamp="2026-10-06T00:00:00Z",
            ),
        )
        review_task = ReviewTaskGenerator(projects).generate(
            handle.project_id, artifact.artifact_id, 1
        ).task
        finding = ReviewIssue.from_dict({
            "issue_id": "ISSUE-0001",
            "fingerprint": "r9.4-acceptance-fingerprint",
            "severity": "major",
            "category": "engineering",
            "field": "mass_kg",
            "reason": "Review found a correction is required",
            "required_action": "Return the issue to the artifact producer",
        })
        before_tasks = tasks.list_tasks(handle.project_id)
        first = IssueOwnerRouter(projects).route(
            handle.project_id, review_task.task_id, finding
        )
        restarted = ProjectStore(projects.projects_root)
        second = IssueOwnerRouter(restarted).route(
            handle.project_id, review_task.task_id, finding
        )
        assert first == second
        assert first.owner_task_id == producer.task_id
        assert first.owner_role == "uav_systems_engineer"
        assert first.issue_id == finding.issue_id
        assert first.target_artifact.entity_id == artifact.artifact_id
        assert first.target_artifact.version == 1
        assert tasks.list_tasks(handle.project_id) == before_tasks

    print(
        "ISSUE_OWNER_ROUTING=PASS "
        "source=review_task target=exact_artifact_version owner_source=artifact_producer "
        "owner_role=uav_systems_engineer owner_task=producer_task "
        "semantic_issue_routing=none restart=PASS writes=none issue_persistence=none "
        "rework_task=reserved_for_R9_5 max_rework=reserved_for_R9_6 "
        "duplicate_suppression=reserved_for_R9_7 provider_calls=none task_execution=none "
        "cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
