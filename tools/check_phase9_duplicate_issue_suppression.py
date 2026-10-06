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
    DUPLICATE_ISSUE_SUPPRESSION_CONTRACT_VERSION,
    DuplicateIssueSuppressor,
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
        "reason": "Repeated root-cause finding",
        "required_action": "Return the finding to the artifact producer",
    })


def main() -> int:
    assert DUPLICATE_ISSUE_SUPPRESSION_CONTRACT_VERSION == "1.0"

    with tempfile.TemporaryDirectory(prefix="r9_7_acceptance_") as td:
        projects = ProjectStore(Path(td) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        producer = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Produce R9.7 review target",
                role="uav_systems_engineer",
                priority=12,
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
                content=b'{"acceptance":"r9.7"}\n',
                created_timestamp="2026-10-06T00:00:00Z",
            ),
        )
        review_task = ReviewTaskGenerator(projects).generate(
            handle.project_id, artifact.artifact_id, artifact.version
        ).task

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
        assert duplicate.reused_existing is True
        assert duplicate.suppressed_duplicate is True
        assert duplicate.task.task_id == first.task.task_id
        assert len(tasks.list_tasks(handle.project_id)) == 3

        decision = DuplicateIssueSuppressor(projects).evaluate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0003", "shared-root"),
        )
        assert decision.duplicate is True
        assert decision.existing_issue_id is not None
        assert decision.existing_issue_id.value == "ISSUE-0001"
        assert decision.existing_task_id == first.task.task_id

        after_duplicate_budget = MaxReworkGate(projects).evaluate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0004", "second-root"),
        )
        assert after_duplicate_budget.rework_attempts == 1
        assert after_duplicate_budget.allowed is True

        second_unique = generator.generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0004", "second-root"),
        )
        assert second_unique.suppressed_duplicate is False
        assert second_unique.task.task_id != first.task.task_id

        try:
            generator.generate(
                handle.project_id,
                review_task.task_id,
                make_issue("ISSUE-0005", "third-root"),
            )
        except MaxReworkGateExceeded as exc:
            assert exc.decision.target_status is TaskState.NEEDS_HUMAN
            assert exc.decision.rework_attempts == 2
        else:
            raise AssertionError("distinct third root cause was not max-rework blocked")

        count = len(tasks.list_tasks(handle.project_id))
        restarted = ProjectStore(projects.projects_root)
        duplicate_after_restart = ReworkTaskGenerator(restarted).generate(
            handle.project_id,
            review_task.task_id,
            make_issue("ISSUE-0006", "shared-root"),
        )
        assert duplicate_after_restart.suppressed_duplicate is True
        assert duplicate_after_restart.task.task_id == first.task.task_id
        assert len(TaskStore(restarted).list_tasks(handle.project_id)) == count

    print(
        "DUPLICATE_ISSUE_SUPPRESSION=PASS "
        "contract_version=1.0 identity=fingerprint lineage=review_task+exact_artifact "
        "cross_issue_duplicate=SUPPRESSED reused_task=PASS budget_consumed=false "
        "different_fingerprint=independent max_rework_attempts=2 third_unique=NEEDS_HUMAN "
        "restart=PASS writes_on_duplicate=none issue_record_persistence=none "
        "provider_calls=none task_execution=none phase9=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
