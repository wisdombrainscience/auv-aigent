from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys

import pytest

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts import ReviewIssue
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectStore
from ai.review import (
    MAX_REWORK_ATTEMPTS,
    MAX_REWORK_GATE_CONTRACT_VERSION,
    MAX_REWORK_TASK_SCAN,
    MaxReworkGate,
    MaxReworkGateDecision,
    MaxReworkGateExceeded,
    ReviewTaskGenerator,
    ReworkTaskGenerationConflictError,
    ReworkTaskGenerator,
)
from ai.tasks.store import TaskCreateInput, TaskStore


def issue(issue_id: str, fingerprint: str) -> ReviewIssue:
    return ReviewIssue.from_dict({
        "issue_id": issue_id,
        "fingerprint": fingerprint,
        "severity": "major",
        "category": "engineering",
        "field": "mass_kg",
        "reason": "Review requires targeted rework",
        "required_action": "Return the finding to the artifact producer",
    })


def make_target(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    producer = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Produce R9.6 review target",
            role="uav_systems_engineer",
            priority=11,
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
            content=b'{"phase":"r9.6"}\n',
            created_timestamp="2026-10-06T00:00:00Z",
        ),
    )
    review_task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, artifact.version
    ).task
    return projects, handle, tasks, producer, artifact, review_task


def test_contract_constants_match_frozen_baseline() -> None:
    assert MAX_REWORK_GATE_CONTRACT_VERSION == "1.0"
    assert MAX_REWORK_ATTEMPTS == 2
    assert MAX_REWORK_TASK_SCAN == 4096


def test_initial_budget_allows_first_rework(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact, review_task = make_target(tmp_path)
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    assert decision.rework_attempts == 0
    assert decision.max_rework_attempts == 2
    assert decision.allowed is True
    assert decision.target_status is None
    assert decision.target_artifact.entity_id == artifact.artifact_id
    assert decision.target_artifact.version == artifact.version


def test_budget_after_first_attempt_allows_second(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2")
    )
    assert decision.rework_attempts == 1
    assert decision.allowed is True
    assert decision.target_status is None


def test_budget_at_two_routes_new_request_to_needs_human(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1"))
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2"))
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0003", "root-3")
    )
    assert decision.rework_attempts == 2
    assert decision.allowed is False
    assert decision.target_status is TaskState.NEEDS_HUMAN


def test_third_rework_is_blocked_before_persistence(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1"))
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2"))
    before = tasks.list_tasks(handle.project_id)
    with pytest.raises(MaxReworkGateExceeded) as excinfo:
        generator.generate(
            handle.project_id, review_task.task_id, issue("ISSUE-0003", "root-3")
        )
    after = tasks.list_tasks(handle.project_id)
    assert after == before
    assert excinfo.value.decision.rework_attempts == 2
    assert excinfo.value.decision.target_status is TaskState.NEEDS_HUMAN


def test_existing_issue_remains_idempotent_after_budget_exhaustion(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    first = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2"))
    count = len(tasks.list_tasks(handle.project_id))
    repeated = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    assert repeated.reused_existing is True
    assert repeated.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == count


def test_exhausted_gate_survives_restart(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1"))
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2"))
    before = len(tasks.list_tasks(handle.project_id))

    restarted = ProjectStore(projects.projects_root)
    with pytest.raises(MaxReworkGateExceeded) as excinfo:
        ReworkTaskGenerator(restarted).generate(
            handle.project_id, review_task.task_id, issue("ISSUE-0003", "root-3")
        )
    assert excinfo.value.decision.target_status is TaskState.NEEDS_HUMAN
    assert len(TaskStore(restarted).list_tasks(handle.project_id)) == before


def test_duplicate_fingerprint_is_suppressed_before_budget_evaluation(tmp_path: Path) -> None:
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

    second_unique = generator.generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0003", "root-2")
    )
    assert second_unique.task.task_id != first.task.task_id
    with pytest.raises(MaxReworkGateExceeded):
        generator.generate(
            handle.project_id, review_task.task_id, issue("ISSUE-0004", "root-3")
        )
    assert len(tasks.list_tasks(handle.project_id)) == 4


def test_task_retry_budget_remains_distinct_from_review_rework_budget(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, _artifact, review_task = make_target(tmp_path)
    first = ReworkTaskGenerator(projects).generate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    ).task
    assert producer.max_attempts == 3
    assert first.max_attempts == 3
    assert MAX_REWORK_ATTEMPTS == 2


def test_gate_evaluation_is_read_only(tmp_path: Path) -> None:
    projects, handle, tasks, producer, _artifact, review_task = make_target(tmp_path)
    before = tasks.list_tasks(handle.project_id)
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    after = tasks.list_tasks(handle.project_id)
    assert decision.allowed is True
    assert after == before
    assert tasks.open_task(handle.project_id, producer.task_id).status is TaskState.CREATED
    assert tasks.open_task(handle.project_id, review_task.task_id).status is TaskState.CREATED


def test_budget_is_independent_for_different_exact_review_lineages(tmp_path: Path) -> None:
    projects, handle, tasks, producer, _artifact, review_task = make_target(tmp_path)
    generator = ReworkTaskGenerator(projects)
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1"))
    generator.generate(handle.project_id, review_task.task_id, issue("ISSUE-0002", "root-2"))

    second_artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="ai.agent_result.v1",
            producer_task=producer.task_id,
            input_dependency_versions=(),
            content=b'{"phase":"r9.6-second-lineage"}\n',
            created_timestamp="2026-10-06T00:00:01Z",
        ),
    )
    second_review = ReviewTaskGenerator(projects).generate(
        handle.project_id, second_artifact.artifact_id, second_artifact.version
    ).task
    result = generator.generate(
        handle.project_id, second_review.task_id, issue("ISSUE-0003", "root-3")
    )
    assert result.reused_existing is False
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, second_review.task_id, issue("ISSUE-0004", "root-4")
    )
    assert decision.rework_attempts == 1
    assert decision.allowed is True
    assert len(tasks.list_tasks(handle.project_id)) == 6


def test_malformed_reserved_rework_title_fails_closed(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, review_task = make_target(tmp_path)
    tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Rework MALFORMED",
            role=producer.role,
            priority=producer.priority,
            dependencies=(review_task.task_id,),
            input_artifacts=(review_task.input_artifacts[0],),
            output_schema=producer.output_schema,
            validators=producer.validators,
            review_required=True,
            max_attempts=producer.max_attempts,
        ),
    )
    with pytest.raises(ReworkTaskGenerationConflictError, match="malformed"):
        MaxReworkGate(projects).evaluate(
            handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
        )
    assert artifact.artifact_id == review_task.input_artifacts[0].entity_id


def test_gate_decision_is_immutable(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_target(tmp_path)
    decision = MaxReworkGate(projects).evaluate(
        handle.project_id, review_task.task_id, issue("ISSUE-0001", "root-1")
    )
    assert isinstance(decision, MaxReworkGateDecision)
    with pytest.raises(FrozenInstanceError):
        decision.allowed = False  # type: ignore[misc]


def test_gate_exposes_no_duplicate_suppression_or_execution_authority(tmp_path: Path) -> None:
    projects, *_ = make_target(tmp_path)
    gate = MaxReworkGate(projects)
    for name in (
        "suppress_duplicate", "deduplicate", "persist_issue", "allocate_issue",
        "execute", "run", "provider", "model", "transition_task",
    ):
        assert not hasattr(gate, name)


def test_r9_6_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_max_rework_gate.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "MAX_REWORK_GATE=PASS" in proc.stdout
