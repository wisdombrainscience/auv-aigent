from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sqlite3

import pytest

import ai.orchestrator.persistence as persistence
from ai.contracts.ids import IdKind
from ai.contracts.task_states import TaskState
from ai.orchestrator import (
    APPROVED_DAG_DIRECTORY_NAME,
    APPROVED_DAG_JOURNAL_FILENAME,
    APPROVED_DAG_PERSISTENCE_CONTRACT_VERSION,
    ApprovedDAGConflictError,
    ApprovedDAGReceipt,
    ApprovedDAGStore,
    ApprovedDAGValidationError,
    ProposedTask,
    TaskIdBinding,
    TaskPlanProposal,
    persist_approved_task_dag,
)
from ai.project.requirement_versions import RequirementVersionInput, RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskDAGValidator,
    TaskStore,
)


def _project(tmp_path: Path, *, approved: bool = True):
    projects = ProjectStore((tmp_path / "data" / "projects").resolve())
    handle = projects.create_project()
    RequirementVersionStore(projects).create_initial(
        handle.project_id,
        [RequirementVersionInput(text="Build a deterministic UAV project")],
        status=RequirementSetStatus.APPROVED if approved else RequirementSetStatus.DRAFT,
    )
    return projects, handle


def _task(
    task_id: str,
    *,
    title: str | None = None,
    role: str = "requirements_engineer",
    dependencies: tuple[str, ...] = (),
    max_attempts: int = 3,
) -> ProposedTask:
    return ProposedTask(
        task_id=task_id,
        title=title or f"Plan {task_id}",
        role=role,
        priority=10,
        dependencies=dependencies,
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=True,
        max_attempts=max_attempts,
    )


def _proposal(project_id, *, version: int = 1) -> TaskPlanProposal:
    return TaskPlanProposal(
        project_id=project_id,
        requirement_version=version,
        tasks=(
            _task("TASK-000001", title="Requirements"),
            _task(
                "TASK-000002",
                title="Architecture",
                role="uav_systems_engineer",
                dependencies=("TASK-000001",),
            ),
            _task(
                "TASK-000003",
                title="Review",
                role="reviewer",
                dependencies=("TASK-000001", "TASK-000002"),
            ),
        ),
    )


def _seed_task(projects: ProjectStore, handle) -> None:
    TaskStore(projects).create_task(
        handle.project_id,
        TaskCreateInput(
            title="Existing task",
            role="requirements_engineer",
            priority=1,
            dependencies=(),
            input_artifacts=(),
            output_schema="engineering_task.v1",
            validators=("schema",),
            review_required=True,
            max_attempts=3,
        ),
    )


def _task_sequence(projects: ProjectStore) -> int:
    with sqlite3.connect(projects.database_path) as connection:
        return connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
            (IdKind.TASK.value,),
        ).fetchone()[0]


def test_contract_constants_are_explicit() -> None:
    assert APPROVED_DAG_PERSISTENCE_CONTRACT_VERSION == "1.0"
    assert APPROVED_DAG_DIRECTORY_NAME == "approved_dags"
    assert APPROVED_DAG_JOURNAL_FILENAME == ".approved-dag-transaction.json"


def test_receipt_and_binding_are_immutable() -> None:
    binding = TaskIdBinding("TASK-000001", "TASK-000101")
    receipt = ApprovedDAGReceipt(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        proposal_sha256="a" * 64,
        task_bindings=(binding,),
    )
    with pytest.raises(FrozenInstanceError):
        binding.persisted_task_id = binding.candidate_task_id
    with pytest.raises(FrozenInstanceError):
        receipt.requirement_version = 2


def test_persist_remaps_candidate_ids_atomically_and_preserves_dependencies(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    _seed_task(projects, handle)
    proposal = _proposal(handle.project_id)

    receipt = ApprovedDAGStore(projects).persist(proposal)
    assert [item.candidate_task_id.value for item in receipt.task_bindings] == [
        "TASK-000001", "TASK-000002", "TASK-000003"
    ]
    assert [item.persisted_task_id.value for item in receipt.task_bindings] == [
        "TASK-000002", "TASK-000003", "TASK-000004"
    ]

    tasks = TaskStore(projects)
    created = [tasks.open_task(handle.project_id, item) for item in receipt.task_ids]
    assert all(item.status is TaskState.CREATED and item.attempt == 0 for item in created)
    assert tuple(dep.value for dep in created[1].dependencies) == ("TASK-000002",)
    assert tuple(dep.value for dep in created[2].dependencies) == ("TASK-000002", "TASK-000003")

    report = TaskDAGValidator(projects).validate_project(handle.project_id)
    assert report.node_count == 4
    assert report.edge_count == 3


def test_persist_is_idempotent_in_same_process(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    proposal = _proposal(handle.project_id)
    store = ApprovedDAGStore(projects)
    first = store.persist(proposal)
    sequence = _task_sequence(projects)
    second = store.persist(proposal)
    assert second == first
    assert _task_sequence(projects) == sequence
    assert len(TaskStore(projects).list_tasks(handle.project_id)) == 3


def test_restart_reopens_same_receipt_without_allocating_more_ids(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    proposal = _proposal(handle.project_id)
    first = ApprovedDAGStore(projects).persist(proposal)
    sequence = _task_sequence(projects)
    restarted_projects = ProjectStore(projects.projects_root)
    second = ApprovedDAGStore(restarted_projects).persist(proposal)
    assert second == first
    assert _task_sequence(restarted_projects) == sequence


def test_invalid_plan_is_rejected_before_any_task_write(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    proposal = TaskPlanProposal(
        project_id=handle.project_id,
        requirement_version=1,
        tasks=(_task("TASK-000001", role="future_specialist"),),
    )
    before = _task_sequence(projects)
    with pytest.raises(ApprovedDAGValidationError):
        ApprovedDAGStore(projects).persist(proposal)
    assert _task_sequence(projects) == before
    assert TaskStore(projects).list_tasks(handle.project_id) == ()


def test_draft_requirement_snapshot_is_rejected(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path, approved=False)
    with pytest.raises(ApprovedDAGValidationError, match="not APPROVED"):
        ApprovedDAGStore(projects).persist(_proposal(handle.project_id))
    assert TaskStore(projects).list_tasks(handle.project_id) == ()


def test_stale_requirement_version_is_rejected(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    RequirementVersionStore(projects).append_version(
        handle.project_id,
        [RequirementVersionInput(text="Updated requirement")],
        status=RequirementSetStatus.APPROVED,
    )
    with pytest.raises(ApprovedDAGValidationError, match="not the latest"):
        ApprovedDAGStore(projects).persist(_proposal(handle.project_id, version=1))


def test_current_approved_requirement_version_two_can_persist(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    RequirementVersionStore(projects).append_version(
        handle.project_id,
        [RequirementVersionInput(text="Updated requirement")],
        status=RequirementSetStatus.APPROVED,
    )
    receipt = ApprovedDAGStore(projects).persist(_proposal(handle.project_id, version=2))
    assert receipt.requirement_version == 2


def test_forward_reference_dependency_is_remapped(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    proposal = TaskPlanProposal(
        project_id=handle.project_id,
        requirement_version=1,
        tasks=(
            _task("TASK-000001", dependencies=("TASK-000003",)),
            _task("TASK-000002"),
            _task("TASK-000003"),
        ),
    )
    receipt = ApprovedDAGStore(projects).persist(proposal)
    mapping = receipt.mapping()
    first = TaskStore(projects).open_task(handle.project_id, mapping["TASK-000001"])
    assert first.dependencies == (mapping["TASK-000003"],)


def test_controlled_insert_failure_rolls_back_all_tasks_and_sequence(tmp_path: Path, monkeypatch) -> None:
    projects, handle = _project(tmp_path)
    proposal = _proposal(handle.project_id)
    before = _task_sequence(projects)
    original = persistence._insert_persisted_record
    count = 0

    def fail_second(connection, record):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected insert failure")
        return original(connection, record)

    monkeypatch.setattr(persistence, "_insert_persisted_record", fail_second)
    with pytest.raises(RuntimeError, match="injected insert failure"):
        ApprovedDAGStore(projects).persist(proposal)
    assert _task_sequence(projects) == before
    assert TaskStore(projects).list_tasks(handle.project_id) == ()
    journal = handle.layout.data_directories["tasks"] / APPROVED_DAG_DIRECTORY_NAME / APPROVED_DAG_JOURNAL_FILENAME
    assert not journal.exists()


def test_restart_recovers_commit_when_receipt_write_was_interrupted(tmp_path: Path, monkeypatch) -> None:
    projects, handle = _project(tmp_path)
    proposal = _proposal(handle.project_id)
    original = persistence._atomic_write_new
    receipt_failures = 0

    def fail_receipt(path: Path, raw: bytes):
        nonlocal receipt_failures
        if path.name != APPROVED_DAG_JOURNAL_FILENAME and path.parent.name == APPROVED_DAG_DIRECTORY_NAME:
            receipt_failures += 1
            if receipt_failures == 1:
                raise OSError("injected receipt interruption")
        return original(path, raw)

    monkeypatch.setattr(persistence, "_atomic_write_new", fail_receipt)
    with pytest.raises(OSError, match="injected receipt interruption"):
        ApprovedDAGStore(projects).persist(proposal)
    assert len(TaskStore(projects).list_tasks(handle.project_id)) == 3
    sequence = _task_sequence(projects)

    monkeypatch.setattr(persistence, "_atomic_write_new", original)
    restarted = ProjectStore(projects.projects_root)
    receipt = ApprovedDAGStore(restarted).persist(proposal)
    assert len(receipt.task_ids) == 3
    assert _task_sequence(restarted) == sequence
    assert len(TaskStore(restarted).list_tasks(handle.project_id)) == 3


def test_receipt_tamper_fails_closed(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    proposal = _proposal(handle.project_id)
    receipt = ApprovedDAGStore(projects).persist(proposal)
    path = handle.layout.data_directories["tasks"] / APPROVED_DAG_DIRECTORY_NAME / f"{receipt.proposal_sha256}.json"
    raw = path.read_text(encoding="utf-8").replace("TASK-000001", "TASK-000099", 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ApprovedDAGConflictError):
        ApprovedDAGStore(projects).persist(proposal)


def test_receipt_digest_is_proposal_sha256(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    proposal = _proposal(handle.project_id)
    receipt = ApprovedDAGStore(projects).persist(proposal)
    assert receipt.proposal_sha256 == hashlib.sha256(proposal.to_json_bytes()).hexdigest()


def test_persistence_keeps_task_registry_and_project_schema_table_counts(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    ApprovedDAGStore(projects).persist(_proposal(handle.project_id))
    registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        tables = tuple(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ))
    assert tables == ("task_dependency_edges", "task_registry_meta", "tasks")
    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ))
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_persist_function_wrapper_uses_same_contract(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    receipt = persist_approved_task_dag(projects, _proposal(handle.project_id))
    assert isinstance(receipt, ApprovedDAGReceipt)
    assert len(receipt.task_bindings) == 3


def test_persist_does_not_advance_task_lifecycle(tmp_path: Path) -> None:
    projects, handle = _project(tmp_path)
    receipt = ApprovedDAGStore(projects).persist(_proposal(handle.project_id))
    records = [TaskStore(projects).open_task(handle.project_id, task_id) for task_id in receipt.task_ids]
    assert {record.status for record in records} == {TaskState.CREATED}
    assert {record.attempt for record in records} == {0}


def test_binding_rejects_non_task_ids() -> None:
    with pytest.raises(ApprovedDAGConflictError):
        TaskIdBinding("ART-000001", "TASK-000001")
    with pytest.raises(ApprovedDAGConflictError):
        TaskIdBinding("TASK-000001", "REQ-000001")


def test_receipt_rejects_duplicate_mapping() -> None:
    with pytest.raises(ApprovedDAGConflictError):
        ApprovedDAGReceipt(
            project_id="UAV-PROJ-000001",
            requirement_version=1,
            proposal_sha256="b" * 64,
            task_bindings=(
                TaskIdBinding("TASK-000001", "TASK-000010"),
                TaskIdBinding("TASK-000001", "TASK-000011"),
            ),
        )
