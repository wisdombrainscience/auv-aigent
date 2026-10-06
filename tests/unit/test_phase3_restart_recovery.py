from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

import ai.project.transaction as transaction_module
from ai.project.assumptions import (
    Assumption,
    AssumptionSet,
    AssumptionStore,
)
from ai.project.decisions import (
    Decision,
    DecisionSet,
    DecisionStore,
)
from ai.project.facts import (
    Fact,
    FactSet,
    FactStore,
)
from ai.project.requirement_versions import (
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.project.transaction import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    PROJECT_RECOVERY_CONTRACT_VERSION,
    PROJECT_TRANSACTION_JOURNAL_FILENAME,
    PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION,
    ProjectStateTransaction,
    ProjectTransactionConflictError,
    recover_project_state_transaction,
)


class SimulatedProcessCrash(BaseException):
    pass


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return projects, handle


def facts(project_id, mass: float) -> FactSet:
    return FactSet(
        project_id=project_id,
        facts=(
            Fact(
                key="vehicle.mass_kg",
                value=mass,
                provenance="USER_FACT",
            ),
        ),
    )


def assumptions(project_id, drag: float) -> AssumptionSet:
    return AssumptionSet(
        project_id=project_id,
        assumptions=(
            Assumption(
                key="airframe.drag_coefficient",
                value=drag,
            ),
        ),
    )


def decisions(project_id, frame: str) -> DecisionSet:
    return DecisionSet(
        project_id=project_id,
        decisions=(
            Decision(
                key="navigation.internal_frame",
                value=frame,
                rationale="Explicit project decision for restart testing.",
            ),
        ),
    )


def test_recovery_contract_and_bounds_are_explicit() -> None:
    assert PROJECT_RECOVERY_CONTRACT_VERSION == "1.0"
    assert PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION == 1
    assert (
        PROJECT_TRANSACTION_JOURNAL_FILENAME
        == ".project-state-transaction.json"
    )
    assert MAX_TRANSACTION_JOURNAL_BYTES == 12 * 1024 * 1024


def test_complete_project_state_survives_fresh_restart(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    requirement_store = RequirementVersionStore(projects)
    first = requirement_store.create_initial(
        handle.project_id,
        [
            "Payload shall not exceed 2 kg.",
            "Minimum endurance shall be 20 minutes.",
        ],
    )
    keep = first.requirement_set.requirements[0].requirement_id
    second = requirement_store.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=keep,
                text="Payload shall not exceed 2.2 kg.",
            ),
            RequirementVersionInput(
                text="Return-to-home shall be supported.",
            ),
        ],
    )

    current_facts = facts(handle.project_id, 2.5)
    current_assumptions = assumptions(handle.project_id, 0.8)
    current_decisions = decisions(handle.project_id, "LOCAL_NED")

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(current_facts)
    tx.stage_assumptions(current_assumptions)
    tx.stage_decisions(current_decisions)
    tx.commit()

    restarted_projects = ProjectStore(projects.projects_root)
    restarted_requirements = RequirementVersionStore(restarted_projects)

    assert restarted_requirements.open_version(handle.project_id, 1) == first
    assert restarted_requirements.open_latest(handle.project_id) == second
    assert FactStore(restarted_projects).open(handle.project_id) == current_facts
    assert AssumptionStore(restarted_projects).open(handle.project_id) == current_assumptions
    assert DecisionStore(restarted_projects).open(handle.project_id) == current_decisions
    assert recover_project_state_transaction(
        restarted_projects,
        handle.project_id,
    ) is False


def test_interrupted_commit_leaves_recovery_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, handle = make_project(tmp_path)

    FactStore(projects).save(facts(handle.project_id, 2.0))
    AssumptionStore(projects).save(assumptions(handle.project_id, 0.7))
    DecisionStore(projects).save(decisions(handle.project_id, "LOCAL_NED"))

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(facts(handle.project_id, 3.0))
    tx.stage_assumptions(assumptions(handle.project_id, 0.9))
    tx.stage_decisions(decisions(handle.project_id, "BODY_FRD"))

    original = transaction_module._atomic_replace
    target_writes = 0

    def crash_after_two_target_writes(path, raw):
        nonlocal target_writes
        if path.name != PROJECT_TRANSACTION_JOURNAL_FILENAME:
            target_writes += 1
            if target_writes == 3:
                raise SimulatedProcessCrash()
        return original(path, raw)

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        crash_after_two_target_writes,
    )

    with pytest.raises(SimulatedProcessCrash):
        tx.commit()

    journal = (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    )
    assert journal.is_file()

    payload = json.loads(journal.read_text(encoding="utf-8"))
    assert payload["state"] == "PREPARED"
    assert payload["project_id"] == handle.project_id.value
    assert len(payload["files"]) == 6


def test_restart_recovery_rolls_back_interrupted_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, handle = make_project(tmp_path)

    old_facts = facts(handle.project_id, 2.0)
    old_assumptions = assumptions(handle.project_id, 0.7)
    old_decisions = decisions(handle.project_id, "LOCAL_NED")
    FactStore(projects).save(old_facts)
    AssumptionStore(projects).save(old_assumptions)
    DecisionStore(projects).save(old_decisions)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(facts(handle.project_id, 3.0))
    tx.stage_assumptions(assumptions(handle.project_id, 0.9))
    tx.stage_decisions(decisions(handle.project_id, "BODY_FRD"))

    original = transaction_module._atomic_replace
    target_writes = 0

    def crash_mid_commit(path, raw):
        nonlocal target_writes
        if path.name != PROJECT_TRANSACTION_JOURNAL_FILENAME:
            target_writes += 1
            if target_writes == 4:
                raise SimulatedProcessCrash()
        return original(path, raw)

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        crash_mid_commit,
    )
    with pytest.raises(SimulatedProcessCrash):
        tx.commit()
    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        original,
    )

    restarted = ProjectStore(projects.projects_root)
    assert recover_project_state_transaction(
        restarted,
        handle.project_id,
    ) is True

    assert FactStore(restarted).open(handle.project_id) == old_facts
    assert AssumptionStore(restarted).open(handle.project_id) == old_assumptions
    assert DecisionStore(restarted).open(handle.project_id) == old_decisions

    journal = (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    )
    assert not journal.exists()

    second_restart = ProjectStore(projects.projects_root)
    assert recover_project_state_transaction(
        second_restart,
        handle.project_id,
    ) is False
    assert FactStore(second_restart).open(handle.project_id) == old_facts


def test_pending_journal_blocks_new_transaction_until_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, handle = make_project(tmp_path)
    FactStore(projects).save(facts(handle.project_id, 2.0))

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(facts(handle.project_id, 3.0))

    original = transaction_module._atomic_replace
    writes = 0

    def crash(path, raw):
        nonlocal writes
        if path.name != PROJECT_TRANSACTION_JOURNAL_FILENAME:
            writes += 1
            if writes == 2:
                raise SimulatedProcessCrash()
        return original(path, raw)

    monkeypatch.setattr(transaction_module, "_atomic_replace", crash)
    with pytest.raises(SimulatedProcessCrash):
        tx.commit()
    monkeypatch.setattr(transaction_module, "_atomic_replace", original)

    restarted = ProjectStore(projects.projects_root)
    with pytest.raises(
        ProjectTransactionConflictError,
        match="recovery journal",
    ):
        ProjectStateTransaction(restarted, handle.project_id)

    assert recover_project_state_transaction(
        restarted,
        handle.project_id,
    ) is True

    next_tx = ProjectStateTransaction(restarted, handle.project_id)
    next_tx.rollback()


def test_corrupt_journal_fails_closed_and_is_not_deleted(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    journal = (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    )
    journal.write_text('{"corrupt":true}\n', encoding="utf-8")

    with pytest.raises(ProjectTransactionConflictError):
        recover_project_state_transaction(
            projects,
            handle.project_id,
        )

    assert journal.exists()


def test_foreign_project_journal_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    second = projects.create_project()
    journal = (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    )

    payload = {
        "format_version": PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION,
        "project_id": second.project_id.value,
        "state": "PREPARED",
        "files": [
            {
                "name": "facts.json",
                "existed": False,
                "backup_b64": None,
                "backup_sha256": None,
            }
        ],
    }
    journal.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ProjectTransactionConflictError,
        match="project ID mismatch",
    ):
        recover_project_state_transaction(
            projects,
            handle.project_id,
        )


def test_journal_symlink_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")

    journal = (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    )
    journal.symlink_to(external)

    with pytest.raises(
        ProjectTransactionConflictError,
        match="symbolic link",
    ):
        recover_project_state_transaction(
            projects,
            handle.project_id,
        )


def test_unknown_journal_target_is_rejected(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    journal = (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    )
    payload = {
        "format_version": PROJECT_TRANSACTION_JOURNAL_FORMAT_VERSION,
        "project_id": handle.project_id.value,
        "state": "PREPARED",
        "files": [
            {
                "name": "project.json",
                "existed": False,
                "backup_b64": None,
                "backup_sha256": None,
            }
        ],
    }
    journal.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ProjectTransactionConflictError,
        match="target",
    ):
        recover_project_state_transaction(
            projects,
            handle.project_id,
        )


def test_normal_commit_removes_recovery_journal(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(facts(handle.project_id, 2.5))
    tx.commit()

    assert not (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    ).exists()


def test_in_process_failure_rolls_back_and_removes_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, handle = make_project(tmp_path)
    old = facts(handle.project_id, 2.0)
    FactStore(projects).save(old)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(facts(handle.project_id, 3.0))

    original = transaction_module._atomic_replace
    target_writes = 0

    def fail_second_target(path, raw):
        nonlocal target_writes
        if path.name != PROJECT_TRANSACTION_JOURNAL_FILENAME:
            target_writes += 1
            if target_writes == 2:
                raise OSError("injected")
        return original(path, raw)

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        fail_second_target,
    )
    with pytest.raises(OSError, match="injected"):
        tx.commit()
    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        original,
    )

    assert FactStore(projects).open(handle.project_id) == old
    assert not (
        handle.layout.project_dir
        / PROJECT_TRANSACTION_JOURNAL_FILENAME
    ).exists()


def test_restart_recovery_does_not_rewrite_requirement_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, handle = make_project(tmp_path)
    requirement_store = RequirementVersionStore(projects)
    first = requirement_store.create_initial(handle.project_id, ["A"])
    second = requirement_store.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=first.requirement_set.requirements[0].requirement_id,
                text="A2",
            )
        ],
    )

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(facts(handle.project_id, 2.5))

    original = transaction_module._atomic_replace
    target_writes = 0

    def crash(path, raw):
        nonlocal target_writes
        if path.name != PROJECT_TRANSACTION_JOURNAL_FILENAME:
            target_writes += 1
            if target_writes == 2:
                raise SimulatedProcessCrash()
        return original(path, raw)

    monkeypatch.setattr(transaction_module, "_atomic_replace", crash)
    with pytest.raises(SimulatedProcessCrash):
        tx.commit()
    monkeypatch.setattr(transaction_module, "_atomic_replace", original)

    restarted = ProjectStore(projects.projects_root)
    assert recover_project_state_transaction(
        restarted,
        handle.project_id,
    ) is True

    reopened_requirements = RequirementVersionStore(restarted)
    assert reopened_requirements.open_version(handle.project_id, 1) == first
    assert reopened_requirements.open_latest(handle.project_id) == second


def test_restart_recovery_does_not_change_sqlite_schema(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    assert recover_project_state_transaction(
        projects,
        handle.project_id,
    ) is False

    with sqlite3.connect(projects.database_path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        )

    assert tables == PROJECT_SCHEMA_TABLES


def test_recovery_module_surface_has_no_retry_loop() -> None:
    text = Path(transaction_module.__file__).read_text(
        encoding="utf-8"
    )
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
