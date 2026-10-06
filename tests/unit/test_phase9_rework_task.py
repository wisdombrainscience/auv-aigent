from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts import ReviewIssue
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectStore
from ai.review import (
    MAX_REWORK_TASK_SCAN,
    MaxReworkGateExceeded,
    REWORK_TASK_GENERATION_CONTRACT_VERSION,
    REWORK_TASK_LOCK_FILENAME,
    REWORK_TASK_TITLE_PREFIX,
    ReviewTaskGenerator,
    ReworkTaskGenerationConflictError,
    ReworkTaskGenerationResult,
    ReworkTaskGenerator,
)
from ai.tasks.store import TaskCreateInput, TaskStore


def issue(
    issue_id: str = "ISSUE-0001",
    fingerprint: str = "battery-mass-root-cause",
    *,
    severity: str = "major",
    category: str = "engineering",
    field: str | None = "mass_kg",
    reason: str = "Mass budget needs correction",
    required_action: str = "Rework the producer output",
) -> ReviewIssue:
    return ReviewIssue.from_dict({
        "issue_id": issue_id,
        "fingerprint": fingerprint,
        "severity": severity,
        "category": category,
        "field": field,
        "reason": reason,
        "required_action": required_action,
    })


def make_target(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    producer = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Produce review-required engineering output",
            role="uav_systems_engineer",
            priority=17,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema", "finite_values"),
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
            content=b'{"phase":"r9.5"}\n',
            created_timestamp="2026-10-06T00:00:00Z",
        ),
    )
    review_task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, artifact.version
    ).task
    return projects, handle, tasks, producer, artifact, review_task


def test_contract_constants_are_frozen() -> None:
    assert REWORK_TASK_GENERATION_CONTRACT_VERSION == "1.0"
    assert REWORK_TASK_TITLE_PREFIX == "Rework"
    assert REWORK_TASK_LOCK_FILENAME == ".rework-task-generation.lock"
    assert MAX_REWORK_TASK_SCAN == 4096


def test_generate_persists_targeted_owner_task(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, review_task = make_target(tmp_path)
    finding = issue()
    result = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, finding
    )
    task = result.task

    assert result.reused_existing is False
    assert result.route.owner_task_id == producer.task_id
    assert result.route.owner_role == producer.role
    assert task.role == producer.role
    assert task.priority == producer.priority
    assert task.dependencies == (review_task.task_id,)
    assert len(task.input_artifacts) == 1
    assert task.input_artifacts[0].entity_id == artifact.artifact_id
    assert task.input_artifacts[0].version == artifact.version
    assert task.output_schema == producer.output_schema
    assert task.validators == producer.validators
    assert task.review_required is True
    assert task.max_attempts == producer.max_attempts
    assert task.status is TaskState.CREATED
    assert task.attempt == 0
    assert tasks.open_task(handle.project_id, task.task_id) == task


def test_result_is_immutable(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    result = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue()
    )
    with pytest.raises(FrozenInstanceError):
        result.reused_existing = True  # type: ignore[misc]
    assert isinstance(result, ReworkTaskGenerationResult)


def test_title_binds_issue_id_and_fingerprint_digest_without_semantic_text(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact, review_task = make_target(tmp_path)
    finding = issue(
        fingerprint="stable-root-cause",
        reason="DO_NOT_PERSIST_REASON_TEXT",
        required_action="DO_NOT_PERSIST_ACTION_TEXT",
    )
    task = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, finding
    ).task
    digest = hashlib.sha256(finding.fingerprint.encode("utf-8")).hexdigest()
    assert task.title == (
        f"Rework {finding.issue_id.value} {digest} for "
        f"{artifact.artifact_id.value}@v{artifact.version}"
    )
    assert finding.reason not in task.title
    assert finding.required_action not in task.title
    assert finding.fingerprint not in task.title


def test_same_issue_is_idempotent_in_one_process(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    finding = issue()
    first = generator.generate(handle.project_id, review_task.task_id, finding)
    second = generator.generate(handle.project_id, review_task.task_id, finding)
    assert second.reused_existing is True
    assert second.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 3


def test_same_issue_is_idempotent_after_restart(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    finding = issue()
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, finding
    )
    restarted = ProjectStore(projects.projects_root)
    second = ReworkTaskGenerator(restarted).generate(
        handle.project_id, review_task.task_id, finding
    )
    assert second.reused_existing is True
    assert second.task.task_id == first.task.task_id


def test_same_issue_identity_ignores_semantic_wording_changes(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    first_issue = issue(reason="wording one", required_action="action one")
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, first_issue
    )
    semantically_reworded = issue(
        reason="wording two",
        required_action="action two",
        severity="critical",
        category="different",
        field=None,
    )
    second = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, semantically_reworded
    )
    assert second.reused_existing is True
    assert second.task.task_id == first.task.task_id


def test_same_issue_id_with_different_fingerprint_fails_closed(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue(fingerprint="root-a")
    )
    with pytest.raises(ReworkTaskGenerationConflictError, match="canonical issue_id"):
        ReworkTaskGenerator(projects).generate(
            handle.project_id, review_task.task_id, issue(fingerprint="root-b")
        )


def test_different_issue_ids_with_same_fingerprint_are_suppressed_by_r9_7(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id,
        issue("ISSUE-0001", "same-root"),
    )
    second = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id,
        issue("ISSUE-0002", "same-root"),
    )
    assert first.suppressed_duplicate is False
    assert second.reused_existing is True
    assert second.suppressed_duplicate is True
    assert second.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 3


def test_r9_5_generator_is_cumulatively_guarded_by_r9_6_max_rework(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    first = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    second = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2")
    )
    with pytest.raises(MaxReworkGateExceeded) as excinfo:
        generator.generate(
            handle.project_id, review_task.task_id, issue("ISSUE-0003", "root-3")
        )
    assert first.task.task_id != second.task.task_id
    assert excinfo.value.decision.target_status is TaskState.NEEDS_HUMAN
    assert len(tasks.list_tasks(handle.project_id)) == 4


def test_rework_generation_does_not_mutate_source_records(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, review_task = make_target(tmp_path)
    artifacts = ArtifactVersionStore(projects)
    before_producer = tasks.open_task(handle.project_id, producer.task_id)
    before_review = tasks.open_task(handle.project_id, review_task.task_id)
    before_artifact = artifacts.open_version(handle.project_id, artifact.artifact_id, artifact.version)

    ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue()
    )

    assert tasks.open_task(handle.project_id, producer.task_id) == before_producer
    assert tasks.open_task(handle.project_id, review_task.task_id) == before_review
    assert artifacts.open_version(handle.project_id, artifact.artifact_id, artifact.version) == before_artifact


def test_wrong_review_task_is_rejected_via_router(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, _review_task = make_target(tmp_path)
    not_review = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Not a reviewer task",
            role=producer.role,
            priority=producer.priority,
            dependencies=(producer.task_id,),
            input_artifacts=(),
            output_schema=producer.output_schema,
            validators=(),
            review_required=False,
            max_attempts=3,
        ),
    )
    with pytest.raises(ReworkTaskGenerationConflictError, match="owner could not be routed"):
        ReworkTaskGenerator(projects).generate(
            handle.project_id, not_review.task_id, issue()
        )


def test_wrong_project_is_rejected(tmp_path: Path) -> None:
    projects, _handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    other = projects.create_project()
    with pytest.raises(ReworkTaskGenerationConflictError, match="owner could not be routed"):
        ReworkTaskGenerator(projects).generate(
            other.project_id, review_task.task_id, issue()
        )


def test_ambiguous_existing_rework_shape_fails_closed(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, review_task = make_target(tmp_path)
    finding = issue()
    digest = hashlib.sha256(finding.fingerprint.encode("utf-8")).hexdigest()
    tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title=f"Rework {finding.issue_id.value} {digest} for {artifact.artifact_id.value}@v1",
            role=producer.role,
            priority=producer.priority,
            dependencies=(review_task.task_id,),
            input_artifacts=(),  # deliberately conflicts with R9.5 shape
            output_schema=producer.output_schema,
            validators=producer.validators,
            review_required=True,
            max_attempts=producer.max_attempts,
        ),
    )
    with pytest.raises(ReworkTaskGenerationConflictError, match="canonical issue_id"):
        ReworkTaskGenerator(projects).generate(
            handle.project_id, review_task.task_id, finding
        )


def test_recovery_scan_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ai.review.rework as rework_module

    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    monkeypatch.setattr(rework_module, "MAX_REWORK_TASK_SCAN", 1)
    with pytest.raises(ReworkTaskGenerationConflictError, match="MAX_REWORK_TASK_SCAN"):
        ReworkTaskGenerator(projects).generate(
            handle.project_id, review_task.task_id, issue()
        )


def test_lock_symlink_is_rejected(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    project_handle = projects.open_project(handle.project_id)
    lock = project_handle.layout.data_directories["tasks"] / REWORK_TASK_LOCK_FILENAME
    target = project_handle.layout.project_dir / "not-the-lock"
    target.write_text("x", encoding="utf-8")
    lock.symlink_to(target)
    with pytest.raises(ReworkTaskGenerationConflictError, match="symlink"):
        ReworkTaskGenerator(projects).generate(
            handle.project_id, review_task.task_id, issue()
        )


def test_generator_exposes_no_later_gate_or_execution_authority(tmp_path: Path) -> None:
    projects, *_ = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    for name in (
        "max_rework", "enforce_max_rework", "suppress_duplicate", "deduplicate",
        "persist_issue", "allocate_issue", "transition_artifact", "approve", "reject",
        "execute", "run", "provider", "model",
    ):
        assert not hasattr(generator, name)


def test_r9_5_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_rework_task.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "REWORK_TASK_GENERATION=PASS" in proc.stdout
