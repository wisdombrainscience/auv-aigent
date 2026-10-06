#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.project.store import ProjectStore
from ai.review import ReviewTaskGenerator
from ai.tasks.store import TaskCreateInput, TaskStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r9_3_acceptance_") as td:
        projects = ProjectStore(Path(td) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        producer = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Produce review target",
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
                content=b'{"acceptance":"r9.3"}\n',
                created_timestamp="2026-10-06T00:00:00Z",
            ),
        )
        generator = ReviewTaskGenerator(projects)
        first = generator.generate(handle.project_id, artifact.artifact_id, 1)
        second = ReviewTaskGenerator(ProjectStore(projects.projects_root)).generate(
            handle.project_id, artifact.artifact_id, 1
        )
        task = first.task
        assert task.role == "reviewer"
        assert task.output_schema == "review_result.v1"
        assert task.dependencies == (producer.task_id,)
        assert len(task.input_artifacts) == 1
        assert task.status.value == "CREATED"
        assert task.attempt == 0
        assert task.review_required is False
        assert second.task.task_id == task.task_id
        assert second.reused_existing is True
        assert len(tasks.list_tasks(handle.project_id)) == 2

    print(
        "REVIEW_TASK_GENERATION=PASS "
        "role=reviewer output_schema=review_result.v1 source_state=DRAFT "
        "input_artifact=PASS dependency=producer_task status=CREATED attempt=0 "
        "review_required=false idempotent=PASS restart=PASS artifact_transition=none "
        "issue_allocation=none issue_persistence=none owner_routing=reserved_for_R9_4 "
        "rework_task=reserved_for_R9_5 max_rework=reserved_for_R9_6 "
        "duplicate_suppression=reserved_for_R9_7 provider_calls=none task_execution=none "
        "cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
