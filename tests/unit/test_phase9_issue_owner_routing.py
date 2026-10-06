from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys

import pytest

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts import ReviewIssue
from ai.project.store import ProjectStore
from ai.review import (
    ISSUE_OWNER_ROUTING_CONTRACT_VERSION,
    REVIEW_TASK_OUTPUT_SCHEMA,
    IssueOwnerRoute,
    IssueOwnerRouter,
    IssueOwnerRoutingConflictError,
    ReviewTaskGenerator,
)
from ai.tasks.store import TaskCreateInput, TaskStore


CREATED_AT = "2026-10-06T00:00:00Z"


def issue(*, seq: int = 1, severity: str = "major", category: str = "engineering") -> ReviewIssue:
    return ReviewIssue.from_dict({
        "issue_id": f"ISSUE-{seq:04d}",
        "fingerprint": f"fp-{seq}",
        "severity": severity,
        "category": category,
        "field": "mass_kg",
        "reason": "Needs correction",
        "required_action": "Revise source engineering output",
    })


def make_route_target(tmp_path: Path, *, role: str = "uav_systems_engineer"):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    producer = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Produce review-owned artifact",
            role=role,
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
            content=b'{"routing":"r9.4"}\n',
            created_timestamp=CREATED_AT,
        ),
    )
    review_task = ReviewTaskGenerator(projects).generate(
        handle.project_id, artifact.artifact_id, 1
    ).task
    return projects, handle, tasks, producer, artifact, review_task


def test_contract_constant_is_explicit() -> None:
    assert ISSUE_OWNER_ROUTING_CONTRACT_VERSION == "1.0"


def test_route_uses_artifact_producer_task_and_role(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, artifact, review_task = make_route_target(tmp_path)
    route = IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, issue())
    assert route.owner_task_id == producer.task_id
    assert route.owner_role == "uav_systems_engineer"
    assert route.review_task_id == review_task.task_id
    assert route.target_artifact == ArtifactInputVersion(artifact.artifact_id, 1)


def test_route_preserves_issue_identity_without_mutating_issue(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_route_target(tmp_path)
    finding = issue(seq=41)
    before = finding.to_dict()
    route = IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, finding)
    assert route.issue_id == finding.issue_id
    assert route.fingerprint == finding.fingerprint
    assert finding.to_dict() == before


def test_semantic_issue_fields_do_not_choose_owner(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, _artifact, review_task = make_route_target(tmp_path)
    a = IssueOwnerRouter(projects).route(
        handle.project_id, review_task.task_id,
        issue(seq=1, severity="critical", category="mass"),
    )
    b = IssueOwnerRouter(projects).route(
        handle.project_id, review_task.task_id,
        issue(seq=2, severity="minor", category="software"),
    )
    assert a.owner_task_id == b.owner_task_id == producer.task_id
    assert a.owner_role == b.owner_role == producer.role


def test_requirements_engineer_can_be_deterministic_owner(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, _artifact, review_task = make_route_target(
        tmp_path, role="requirements_engineer"
    )
    route = IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, issue())
    assert route.owner_task_id == producer.task_id
    assert route.owner_role == "requirements_engineer"


def test_route_is_restart_deterministic(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, _artifact, review_task = make_route_target(tmp_path)
    first = IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, issue())
    restarted = ProjectStore(projects.projects_root)
    second = IssueOwnerRouter(restarted).route(handle.project_id, review_task.task_id, issue())
    assert first == second
    assert second.owner_task_id == producer.task_id


def test_route_does_not_write_tasks_or_artifact_state(tmp_path: Path) -> None:
    projects, handle, tasks, _producer, artifact, review_task = make_route_target(tmp_path)
    before_tasks = tasks.list_tasks(handle.project_id)
    before_artifact = ArtifactVersionStore(projects).open_version(
        handle.project_id, artifact.artifact_id, 1
    )
    IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, issue())
    after_tasks = tasks.list_tasks(handle.project_id)
    after_artifact = ArtifactVersionStore(projects).open_version(
        handle.project_id, artifact.artifact_id, 1
    )
    assert after_tasks == before_tasks
    assert after_artifact == before_artifact


def test_route_object_is_immutable(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_route_target(tmp_path)
    route = IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, issue())
    with pytest.raises(FrozenInstanceError):
        route.owner_role = "coordinator"  # type: ignore[misc]


def test_route_requires_review_issue(tmp_path: Path) -> None:
    projects, handle, _tasks, _producer, _artifact, review_task = make_route_target(tmp_path)
    with pytest.raises(TypeError, match="ReviewIssue"):
        IssueOwnerRouter(projects).route(handle.project_id, review_task.task_id, {})  # type: ignore[arg-type]


def test_non_reviewer_task_is_rejected(tmp_path: Path) -> None:
    projects, handle, _tasks, producer, _artifact, _review_task = make_route_target(tmp_path)
    with pytest.raises(IssueOwnerRoutingConflictError, match="reviewer role"):
        IssueOwnerRouter(projects).route(handle.project_id, producer.task_id, issue())


def test_manual_reviewer_task_with_wrong_schema_is_rejected(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, _review_task = make_route_target(tmp_path)
    manual = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title=f"Review {artifact.artifact_id.value}@v1",
            role="reviewer",
            priority=producer.priority,
            dependencies=(producer.task_id,),
            input_artifacts=(ArtifactInputVersion(artifact.artifact_id, 1),),
            output_schema="engineering_task.v1",
            validators=(),
            review_required=False,
            max_attempts=3,
        ),
    )
    with pytest.raises(IssueOwnerRoutingConflictError, match="output schema"):
        IssueOwnerRouter(projects).route(handle.project_id, manual.task_id, issue())


def test_review_task_dependency_must_match_artifact_producer(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, _review_task = make_route_target(tmp_path)
    other = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Other owner",
            role="requirements_engineer",
            priority=producer.priority,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=(),
            review_required=True,
            max_attempts=3,
        ),
    )
    manual = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title=f"Review {artifact.artifact_id.value}@v1",
            role="reviewer",
            priority=producer.priority,
            dependencies=(other.task_id,),
            input_artifacts=(ArtifactInputVersion(artifact.artifact_id, 1),),
            output_schema=REVIEW_TASK_OUTPUT_SCHEMA,
            validators=(),
            review_required=False,
            max_attempts=3,
        ),
    )
    with pytest.raises(IssueOwnerRoutingConflictError, match="does not match artifact producer"):
        IssueOwnerRouter(projects).route(handle.project_id, manual.task_id, issue())


def test_unknown_owner_role_is_rejected(tmp_path: Path) -> None:
    projects, handle, tasks, producer, artifact, _review_task = make_route_target(tmp_path)
    # Build a structurally valid review-owned artifact from a task role that the
    # frozen MVP RoleRegistry does not enable. TaskStore itself does not own role policy.
    unknown = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title="Unknown specialist producer",
            role="future_specialist",
            priority=producer.priority,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=(),
            review_required=True,
            max_attempts=3,
        ),
    )
    artifact2 = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="ai.agent_result.v1",
            producer_task=unknown.task_id,
            input_dependency_versions=(),
            content=b'{"routing":"unknown-owner"}\n',
            created_timestamp="2026-10-06T00:01:00Z",
        ),
    )
    manual = tasks.create_task(
        handle.project_id,
        TaskCreateInput(
            title=f"Review {artifact2.artifact_id.value}@v1",
            role="reviewer",
            priority=unknown.priority,
            dependencies=(unknown.task_id,),
            input_artifacts=(ArtifactInputVersion(artifact2.artifact_id, 1),),
            output_schema=REVIEW_TASK_OUTPUT_SCHEMA,
            validators=(),
            review_required=False,
            max_attempts=3,
        ),
    )
    with pytest.raises(IssueOwnerRoutingConflictError, match="not an enabled owner"):
        IssueOwnerRouter(projects).route(handle.project_id, manual.task_id, issue())


def test_wrong_project_is_rejected(tmp_path: Path) -> None:
    projects, _handle, _tasks, _producer, _artifact, review_task = make_route_target(tmp_path)
    other = projects.create_project()
    with pytest.raises(IssueOwnerRoutingConflictError, match="review task"):
        IssueOwnerRouter(projects).route(other.project_id, review_task.task_id, issue())


def test_router_exposes_no_rework_or_mutation_authority(tmp_path: Path) -> None:
    projects, *_ = make_route_target(tmp_path)
    router = IssueOwnerRouter(projects)
    for name in (
        "create_rework_task", "persist_issue", "allocate_issue", "max_rework",
        "suppress_duplicate", "transition_artifact", "approve", "reject", "execute",
        "run", "provider", "model",
    ):
        assert not hasattr(router, name)


def test_r9_4_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_issue_owner_routing.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "ISSUE_OWNER_ROUTING=PASS" in proc.stdout
