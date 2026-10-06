from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts import ReviewIssue
from ai.project.store import ProjectStore
from ai.review import (
    DUPLICATE_ISSUE_SUPPRESSION_CONTRACT_VERSION,
    MAX_REWORK_TASK_SCAN,
    DuplicateIssueSuppressionDecision,
    DuplicateIssueSuppressor,
    MaxReworkGate,
    ReworkTaskGenerationConflictError,
    ReworkTaskGenerator,
    ReviewTaskGenerator,
)
from ai.tasks.store import TaskCreateInput, TaskStore


def issue(issue_id: str, fingerprint: str) -> ReviewIssue:
    return ReviewIssue.from_dict({
        "issue_id": issue_id,
        "fingerprint": fingerprint,
        "severity": "major",
        "category": "engineering",
        "field": "mass_kg",
        "reason": "Repeated root-cause finding",
        "required_action": "Return the finding to the artifact producer",
    })


def make_target(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    producer = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Produce R9.7 review target",
            role="uav_systems_engineer",
            priority=13,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema", "finite_values"),
            review_required=True,
            max_attempts=3,
        ),
    )
    artifacts = ArtifactVersionStore(projects)
    artifact = artifacts.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="ai.agent_result.v1",
            producer_task=producer.task_id,
            input_dependency_versions=(),
            content=b'{"phase":"r9.7"}\n',
            created_timestamp="2026-10-06T00:00:00Z",
        ),
    )
    review_task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, artifact.version
    ).task
    return projects, handle, tasks, producer, artifact, review_task


def test_contract_constants_are_frozen() -> None:
    assert DUPLICATE_ISSUE_SUPPRESSION_CONTRACT_VERSION == "1.0"
    assert MAX_REWORK_TASK_SCAN == 4096


def test_new_fingerprint_is_not_duplicate(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact, review_task = make_target(tmp_path)
    decision = DuplicateIssueSuppressor(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-a")
    )
    assert decision.duplicate is False
    assert decision.existing_issue_id is None
    assert decision.existing_task_id is None
    assert decision.target_artifact.entity_id == artifact.artifact_id
    assert decision.target_artifact.version == artifact.version
    assert decision.fingerprint_digest == hashlib.sha256(b"root-a").hexdigest()


def test_cross_issue_duplicate_fingerprint_reuses_existing_task(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    first = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "same-root")
    )
    duplicate = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "same-root")
    )
    assert duplicate.reused_existing is True
    assert duplicate.suppressed_duplicate is True
    assert duplicate.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 3


def test_suppression_decision_reports_original_issue_and_task(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "same-root")
    )
    decision = DuplicateIssueSuppressor(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "same-root")
    )
    assert decision.duplicate is True
    assert decision.existing_issue_id is not None
    assert decision.existing_issue_id.value == "ISSUE-0001"
    assert decision.existing_task_id == first.task.task_id


def test_duplicate_does_not_consume_max_rework_budget(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "same-root")
    )
    generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "same-root")
    )
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0003", "other-root")
    )
    assert decision.rework_attempts == 1
    assert decision.allowed is True


def test_duplicate_is_reused_even_after_two_unique_attempts_fill_budget(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    first = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-a")
    )
    generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-b")
    )
    count = len(tasks.list_tasks(handle.project_id))
    duplicate = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0003", "root-a")
    )
    assert duplicate.suppressed_duplicate is True
    assert duplicate.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == count


def test_duplicate_suppression_survives_restart(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-a")
    )
    restarted = ProjectStore(projects.projects_root)
    duplicate = ReworkTaskGenerator(restarted).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-a")
    )
    assert duplicate.suppressed_duplicate is True
    assert duplicate.task.task_id == first.task.task_id


def test_same_fingerprint_in_different_review_lineage_is_independent(tmp_path: Path) -> None:
    projects, handle, tasks, producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    first = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "same-root")
    )
    second_artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="ai.agent_result.v1",
            producer_task=producer.task_id,
            input_dependency_versions=(),
            content=b'{"phase":"r9.7-second-lineage"}\n',
            created_timestamp="2026-10-06T00:00:01Z",
        ),
    )
    second_review = ReviewTaskGenerator(projects).generate(
        handle.project_id, second_artifact.artifact_id, second_artifact.version
    ).task
    second = generator.generate(
        handle.project_id, second_review.task_id, issue("ISSUE-0002", "same-root")
    )
    assert second.reused_existing is False
    assert second.suppressed_duplicate is False
    assert second.task.task_id != first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 5


def test_same_issue_id_with_changed_fingerprint_still_fails_closed(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-a")
    )
    with pytest.raises(ReworkTaskGenerationConflictError, match="canonical issue_id"):
        generator.generate(
            handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-b")
        )


def test_suppressor_is_read_only(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-a")
    )
    before = tasks.list_tasks(handle.project_id)
    DuplicateIssueSuppressor(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-a")
    )
    assert tasks.list_tasks(handle.project_id) == before


def test_legacy_multiple_duplicates_choose_lowest_task_id_without_new_write(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, review_task = make_target(tmp_path)
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "legacy-root")
    )
    digest = hashlib.sha256(b"legacy-root").hexdigest()
    second = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title=(
                f"Rework ISSUE-0002 {digest} for "
                f"{artifact.artifact_id.value}@v{artifact.version}"
            ),
            role=producer.role,
            priority=producer.priority,
            dependencies=(review_task.task_id,),
            input_artifacts=(review_task.input_artifacts[0],),
            output_schema=producer.output_schema,
            validators=producer.validators,
            review_required=producer.review_required,
            max_attempts=producer.max_attempts,
        ),
    )
    before = len(tasks.list_tasks(handle.project_id))
    decision = DuplicateIssueSuppressor(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0003", "legacy-root")
    )
    assert decision.duplicate is True
    assert decision.existing_task_id == first.task.task_id
    assert first.task.task_id.value < second.task_id.value
    result = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0003", "legacy-root")
    )
    assert result.suppressed_duplicate is True
    assert result.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == before


def test_decision_is_immutable(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    decision = DuplicateIssueSuppressor(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-a")
    )
    assert isinstance(decision, DuplicateIssueSuppressionDecision)
    with pytest.raises(FrozenInstanceError):
        decision.duplicate = True  # type: ignore[misc]


def test_suppressor_exposes_no_persistence_or_execution_authority(tmp_path: Path) -> None:
    projects, *_ = make_target(tmp_path)
    suppressor = DuplicateIssueSuppressor(projects)
    for name in (
        "persist_issue", "allocate_issue", "create_task", "transition_task",
        "execute", "run", "provider", "model",
    ):
        assert not hasattr(suppressor, name)


def test_r9_7_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_duplicate_issue_suppression.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "DUPLICATE_ISSUE_SUPPRESSION=PASS" in proc.stdout
