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
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectStore
from ai.review import (
    MAX_REWORK_ATTEMPTS,
    MAX_REWORK_GATE_CONTRACT_VERSION,
    MaxReworkGate,
    MaxReworkGateExceeded,
    ReviewTaskGenerator,
    ReworkTaskGenerator,
)
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
    assert MAX_REWORK_GATE_CONTRACT_VERSION == "1.0"
    assert MAX_REWORK_ATTEMPTS == 2

    with tempfile.TemporaryDirectory(prefix="r9_6_acceptance_") as td:
        projects = ProjectStore(Path(td) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        producer = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Produce R9.6 review target",
                role="uav_systems_engineer",
                priority=9,
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
                content=b'{"acceptance":"r9.6"}\n',
                created_timestamp="2026-10-06T00:00:00Z",
            ),
        )
        review_task = ReviewTaskGenerator(projects).generate(
            handle.project_id, artifact.artifact_id, artifact.version
        ).task

        gate = MaxReworkGate(projects)
        before = gate.evaluate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0001", "shared-root"),
        )
        assert before.rework_attempts == 0 and before.allowed

        generator = ReworkTaskGenerator(projects)
        first = generator.generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0001", "shared-root"),
        )
        duplicate = generator.generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0002", "shared-root"),
        )
        assert duplicate.suppressed_duplicate is True
        assert duplicate.task.task_id == first.task.task_id
        second = generator.generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0003", "second-root"),
        )
        assert first.task.status is TaskState.CREATED
        assert second.task.status is TaskState.CREATED
        assert first.task.max_attempts == second.task.max_attempts == producer.max_attempts == 3

        exhausted = gate.evaluate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0004", "third-root"),
        )
        assert exhausted.rework_attempts == 2
        assert exhausted.allowed is False
        assert exhausted.target_status is TaskState.NEEDS_HUMAN

        count_before = len(tasks.list_tasks(handle.project_id))
        try:
            generator.generate(
                handle.project_id,
                review_task.task_id,
                make_issue("ISSUE-0004", "third-root"),
            )
        except MaxReworkGateExceeded as exc:
            assert exc.decision.target_status is TaskState.NEEDS_HUMAN
            assert exc.decision.rework_attempts == 2
        else:
            raise AssertionError("third rework task was not blocked")
        assert len(tasks.list_tasks(handle.project_id)) == count_before == 4

        repeated = generator.generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0001", "shared-root"),
        )
        assert repeated.reused_existing is True
        assert repeated.task.task_id == first.task.task_id
        assert len(tasks.list_tasks(handle.project_id)) == count_before

        restarted = ProjectStore(projects.projects_root)
        try:
            ReworkTaskGenerator(restarted).generate(
                handle.project_id,
                review_task.task_id,
                make_issue("ISSUE-0004", "third-root"),
            )
        except MaxReworkGateExceeded as exc:
            assert exc.decision.target_status is TaskState.NEEDS_HUMAN
        else:
            raise AssertionError("restart did not preserve max-rework gate")
        assert len(TaskStore(restarted).list_tasks(handle.project_id)) == count_before

    print(
        "MAX_REWORK_GATE=PASS "
        "contract_version=1.0 max_rework_attempts=2 lineage=review_task+exact_artifact "
        "first=CREATED second=CREATED third=NEEDS_HUMAN no_write=PASS "
        "existing_retry=PASS restart=PASS task_max_attempts=3 "
        "duplicate_suppression=active_R9_7 issue_persistence=none "
        "provider_calls=none task_execution=none cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
