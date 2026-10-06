from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from ai.artifacts import ArtifactLifecycleStore, ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectStore
from ai.review import (
    MAX_REVIEW_TASK_SCAN,
    REVIEW_TASK_GENERATION_CONTRACT_VERSION,
    REVIEW_TASK_OUTPUT_SCHEMA,
    REVIEW_TASK_TITLE_PREFIX,
    ReviewTaskGenerationConflictError,
    ReviewTaskGenerator,
)
from ai.tasks.store import TaskCreateInput, TaskStore


CREATED_AT = "2026-10-06T00:00:00Z"


def producer_input(*, review_required: bool = True, priority: int = 7) -> TaskCreateInput:
    return TaskCreateInput(
        title="Produce engineering artifact",
        role="uav_systems_engineer",
        priority=priority,
        dependencies=(),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=(),
        review_required=review_required,
        max_attempts=3,
    )


def make_target(tmp_path: Path, *, review_required: bool = True, priority: int = 7):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    producer = tasks.create_task(
        handle.project_id,
        producer_input(review_required=review_required, priority=priority),
    )
    artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="ai.agent_result.v1",
            producer_task=producer.task_id,
            input_dependency_versions=(),
            content=b'{"result":"draft"}\n',
            created_timestamp=CREATED_AT,
        ),
    )
    return projects, handle, tasks, producer, artifact


def test_contract_constants_are_explicit() -> None:
    assert REVIEW_TASK_GENERATION_CONTRACT_VERSION == "1.0"
    assert REVIEW_TASK_OUTPUT_SCHEMA == "review_result.v1"
    assert REVIEW_TASK_TITLE_PREFIX == "Review"
    assert MAX_REVIEW_TASK_SCAN == 4096


def test_generates_created_reviewer_task_for_draft_artifact(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, artifact = make_target(tmp_path)
    result = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, artifact.version
    )
    task = result.task
    assert result.reused_existing is False
    assert task.role == "reviewer"
    assert task.status is TaskState.CREATED
    assert task.attempt == 0
    assert task.review_required is False
    assert task.max_attempts == 3
    assert task.priority == producer.priority


def test_review_task_references_exact_artifact_version(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, artifact = make_target(tmp_path)
    task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    ).task
    assert len(task.input_artifacts) == 1
    assert task.input_artifacts[0].entity_id == artifact.artifact_id
    assert task.input_artifacts[0].version == 1
    assert task.dependencies == (producer.task_id,)


def test_review_task_uses_r9_3_review_result_schema_metadata(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact = make_target(tmp_path)
    task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    ).task
    assert task.output_schema == "review_result.v1"
    assert task.validators == ()


def test_review_task_title_is_deterministic(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact = make_target(tmp_path)
    task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    ).task
    assert task.title == f"Review {artifact.artifact_id.value}@v1"


def test_generate_is_idempotent_in_same_process(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, artifact = make_target(tmp_path)
    generator = ReviewTaskGenerator(projects)
    first = generator.generate(handle.project_id, artifact.artifact_id, 1)
    second = generator.generate(handle.project_id, artifact.artifact_id, 1)
    assert second.reused_existing is True
    assert second.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 2


def test_generate_is_idempotent_after_restart(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, artifact = make_target(tmp_path)
    first = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = ReviewTaskGenerator(restarted).generate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert second.reused_existing is True
    assert second.task.task_id == first.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 2


def test_existing_review_task_can_be_reopened_after_artifact_becomes_reviewed(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact = make_target(tmp_path)
    first = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    )
    ArtifactLifecycleStore(projects).transition(
        handle.project_id, artifact.artifact_id, 1, ArtifactState.REVIEWED
    )
    reopened = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert reopened.reused_existing is True
    assert reopened.task.task_id == first.task.task_id


def test_new_review_task_requires_draft_artifact(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact = make_target(tmp_path)
    ArtifactLifecycleStore(projects).transition(
        handle.project_id, artifact.artifact_id, 1, ArtifactState.REVIEWED
    )
    with pytest.raises(ReviewTaskGenerationConflictError, match="only for DRAFT"):
        ReviewTaskGenerator(projects).generate(
            handle.project_id, artifact.artifact_id, 1
        )


def test_producer_task_must_require_review(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, artifact = make_target(
        tmp_path, review_required=False
    )
    before = tasks.list_tasks(handle.project_id)
    with pytest.raises(ReviewTaskGenerationConflictError, match="does not require review"):
        ReviewTaskGenerator(projects).generate(handle.project_id, artifact.artifact_id, 1)
    assert tasks.list_tasks(handle.project_id) == before


def test_missing_artifact_fails_without_task_write(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, _artifact = make_target(tmp_path)
    before = tasks.list_tasks(handle.project_id)
    with pytest.raises(ReviewTaskGenerationConflictError, match="target artifact"):
        ReviewTaskGenerator(projects).generate(handle.project_id, "ART-999999", 1)
    assert tasks.list_tasks(handle.project_id) == before


def test_wrong_project_fails_closed(tmp_path: Path) -> None:
    projects, _handle, _tasks, _producer, artifact = make_target(tmp_path)
    other = projects.create_project()
    with pytest.raises(ReviewTaskGenerationConflictError, match="target artifact"):
        ReviewTaskGenerator(projects).generate(other.project_id, artifact.artifact_id, 1)


def test_generated_task_does_not_transition_artifact(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact = make_target(tmp_path)
    ReviewTaskGenerator(projects).generate(handle.project_id, artifact.artifact_id, 1)
    reopened = ArtifactVersionStore(projects).open_version(
        handle.project_id, artifact.artifact_id, 1
    )
    assert reopened.status is ArtifactState.DRAFT
    assert reopened.validation_result is None


def test_generated_task_does_not_execute_or_advance_task_state(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, artifact = make_target(tmp_path)
    generated = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    ).task
    reopened = tasks.open_task(handle.project_id, generated.task_id)
    assert reopened.status is TaskState.CREATED
    assert reopened.attempt == 0


def test_review_task_itself_does_not_request_recursive_review(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, artifact = make_target(tmp_path)
    task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    ).task
    assert task.review_required is False


def test_multiple_artifact_versions_get_distinct_review_tasks(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact = make_target(tmp_path)
    first = ReviewTaskGenerator(projects).generate(handle.project_id, artifact.artifact_id, 1)
    v2 = ArtifactVersionStore(projects).append_version(
        handle.project_id,
        artifact.artifact_id,
        ArtifactVersionInput(
            type=artifact.type,
            producer_task=producer.task_id,
            input_dependency_versions=(),
            content=b'{"result":"draft-v2"}\n',
            created_timestamp="2026-10-06T00:01:00Z",
        ),
    )
    second = ReviewTaskGenerator(projects).generate(handle.project_id, v2.artifact_id, 2)
    assert first.task.task_id != second.task.task_id
    assert len(tasks.list_tasks(handle.project_id)) == 3


def test_conflicting_manual_reviewer_task_for_same_target_is_rejected(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact = make_target(tmp_path)
    from ai.artifacts.schema import ArtifactInputVersion
    target = ArtifactInputVersion(artifact.artifact_id, 1)
    tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Manual review",
            role="reviewer",
            priority=producer.priority,
            dependencies=(producer.task_id,),
            input_artifacts=(target,),
            output_schema="review_result.v1",
            validators=(),
            review_required=False,
            max_attempts=3,
        ),
    )
    with pytest.raises(ReviewTaskGenerationConflictError, match="conflicting reviewer task"):
        ReviewTaskGenerator(projects).generate(handle.project_id, artifact.artifact_id, 1)


def test_generator_exposes_no_issue_routing_or_rework_authority(tmp_path: Path) -> None:
    projects, *_ = make_target(tmp_path)
    generator = ReviewTaskGenerator(projects)
    for name in (
        "allocate_issue", "persist_issue", "route_issue", "create_rework_task",
        "max_rework", "suppress_duplicate", "approve", "reject", "execute",
        "run", "provider", "model",
    ):
        assert not hasattr(generator, name)


def test_r9_3_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_review_task_generation.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "REVIEW_TASK_GENERATION=PASS" in proc.stdout
