from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

import ai.project.transaction as transaction_module
from ai.project.assumptions import (
    Assumption,
    AssumptionSet,
    AssumptionSetNotFoundError,
    AssumptionStore,
)
from ai.project.decisions import (
    Decision,
    DecisionSet,
    DecisionSetNotFoundError,
    DecisionStore,
)
from ai.project.facts import (
    Fact,
    FactSet,
    FactSetNotFoundError,
    FactStore,
)
from ai.project.requirement_versions import (
    RequirementVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.project.transaction import (
    MAX_TRANSACTION_CATEGORIES,
    MAX_TRANSACTION_FILES,
    MAX_TRANSACTION_STAGED_BYTES,
    PROJECT_TRANSACTION_CONTRACT_VERSION,
    ProjectStateTransaction,
    ProjectTransactionConflictError,
    ProjectTransactionState,
    ProjectTransactionStateError,
)


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return projects, handle


def fact_set(project_id, mass: float = 2.5) -> FactSet:
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


def assumption_set(project_id, drag: float = 0.8) -> AssumptionSet:
    return AssumptionSet(
        project_id=project_id,
        assumptions=(
            Assumption(
                key="airframe.drag_coefficient",
                value=drag,
            ),
        ),
    )


def decision_set(project_id, frame: str = "LOCAL_NED") -> DecisionSet:
    return DecisionSet(
        project_id=project_id,
        decisions=(
            Decision(
                key="navigation.internal_frame",
                value=frame,
                rationale="Preserves the existing runtime convention.",
            ),
        ),
    )


def test_contract_and_bounds_are_explicit() -> None:
    assert PROJECT_TRANSACTION_CONTRACT_VERSION == "1.0"
    assert MAX_TRANSACTION_CATEGORIES == 3
    assert MAX_TRANSACTION_FILES == 6
    assert MAX_TRANSACTION_STAGED_BYTES == 8 * 1024 * 1024


def test_new_transaction_is_open_and_empty(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tx = ProjectStateTransaction(projects, handle.project_id)

    assert tx.state is ProjectTransactionState.OPEN
    assert tx.staged_categories == ()
    assert tx.staged_bytes == 0


def test_commit_all_three_categories_atomically(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.stage_assumptions(assumption_set(handle.project_id))
    tx.stage_decisions(decision_set(handle.project_id))
    receipt = tx.commit()

    assert tx.state is ProjectTransactionState.COMMITTED
    assert receipt.categories == (
        "facts",
        "assumptions",
        "decisions",
    )
    assert receipt.file_count == 6
    assert receipt.staged_bytes > 0

    assert FactStore(projects).open(handle.project_id) == fact_set(handle.project_id)
    assert AssumptionStore(projects).open(handle.project_id) == assumption_set(handle.project_id)
    assert DecisionStore(projects).open(handle.project_id) == decision_set(handle.project_id)


def test_transaction_restart_reads_committed_state(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.stage_assumptions(assumption_set(handle.project_id))
    tx.stage_decisions(decision_set(handle.project_id))
    tx.commit()

    restarted_projects = ProjectStore(projects.projects_root)
    assert FactStore(restarted_projects).open(handle.project_id) == fact_set(handle.project_id)
    assert AssumptionStore(restarted_projects).open(handle.project_id) == assumption_set(handle.project_id)
    assert DecisionStore(restarted_projects).open(handle.project_id) == decision_set(handle.project_id)


def test_rollback_discards_staged_state_without_writing(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.rollback()

    assert tx.state is ProjectTransactionState.ROLLED_BACK
    assert tx.staged_categories == ()

    with pytest.raises(FactSetNotFoundError):
        FactStore(projects).open(handle.project_id)


def test_context_manager_without_commit_rolls_back(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    with ProjectStateTransaction(projects, handle.project_id) as tx:
        tx.stage_assumptions(assumption_set(handle.project_id))

    assert tx.state is ProjectTransactionState.ROLLED_BACK
    with pytest.raises(AssumptionSetNotFoundError):
        AssumptionStore(projects).open(handle.project_id)


def test_context_manager_exception_rolls_back(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    with pytest.raises(RuntimeError, match="boom"):
        with ProjectStateTransaction(projects, handle.project_id) as tx:
            tx.stage_decisions(decision_set(handle.project_id))
            raise RuntimeError("boom")

    assert tx.state is ProjectTransactionState.ROLLED_BACK
    with pytest.raises(DecisionSetNotFoundError):
        DecisionStore(projects).open(handle.project_id)


def test_empty_commit_is_rejected(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tx = ProjectStateTransaction(projects, handle.project_id)

    with pytest.raises(
        ProjectTransactionStateError,
        match="empty",
    ):
        tx.commit()

    assert tx.state is ProjectTransactionState.OPEN


def test_same_category_cannot_be_staged_twice(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))

    with pytest.raises(
        ProjectTransactionConflictError,
        match="already staged",
    ):
        tx.stage_facts(fact_set(handle.project_id, mass=2.7))


def test_cross_project_state_is_rejected(tmp_path: Path) -> None:
    projects, first = make_project(tmp_path)
    second = projects.create_project()

    tx = ProjectStateTransaction(projects, first.project_id)

    with pytest.raises(
        ProjectTransactionConflictError,
        match="different project",
    ):
        tx.stage_decisions(decision_set(second.project_id))


def test_commit_cannot_be_reused(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.commit()

    with pytest.raises(ProjectTransactionStateError):
        tx.stage_assumptions(assumption_set(handle.project_id))
    with pytest.raises(ProjectTransactionStateError):
        tx.commit()
    with pytest.raises(ProjectTransactionStateError):
        tx.rollback()


def test_rollback_cannot_be_reused(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.rollback()

    with pytest.raises(ProjectTransactionStateError):
        tx.stage_facts(fact_set(handle.project_id))
    with pytest.raises(ProjectTransactionStateError):
        tx.commit()


def test_visible_write_failure_restores_every_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, handle = make_project(tmp_path)

    old_facts = fact_set(handle.project_id, mass=2.1)
    old_assumptions = assumption_set(handle.project_id, drag=0.7)
    old_decisions = decision_set(handle.project_id, frame="LOCAL_NED")
    FactStore(projects).save(old_facts)
    AssumptionStore(projects).save(old_assumptions)
    DecisionStore(projects).save(old_decisions)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id, mass=3.0))
    tx.stage_assumptions(assumption_set(handle.project_id, drag=0.9))
    tx.stage_decisions(decision_set(handle.project_id, frame="BODY_FRD"))

    original = transaction_module._atomic_replace
    calls = 0

    def fail_on_fourth(path, raw):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected transaction write failure")
        return original(path, raw)

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        fail_on_fourth,
    )

    with pytest.raises(
        OSError,
        match="injected transaction write failure",
    ):
        tx.commit()

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        original,
    )

    # Failed commit keeps the transaction OPEN so the caller can inspect it or
    # explicitly rollback; persistent Project State is restored.
    assert tx.state is ProjectTransactionState.OPEN
    assert FactStore(projects).open(handle.project_id) == old_facts
    assert AssumptionStore(projects).open(handle.project_id) == old_assumptions
    assert DecisionStore(projects).open(handle.project_id) == old_decisions


def test_new_files_are_removed_if_commit_fails(tmp_path: Path, monkeypatch) -> None:
    projects, handle = make_project(tmp_path)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.stage_assumptions(assumption_set(handle.project_id))

    original = transaction_module._atomic_replace
    calls = 0

    def fail_on_third(path, raw):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected failure")
        return original(path, raw)

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        fail_on_third,
    )

    with pytest.raises(OSError, match="injected"):
        tx.commit()

    monkeypatch.setattr(
        transaction_module,
        "_atomic_replace",
        original,
    )

    with pytest.raises(FactSetNotFoundError):
        FactStore(projects).open(handle.project_id)
    with pytest.raises(AssumptionSetNotFoundError):
        AssumptionStore(projects).open(handle.project_id)


def test_symlink_target_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")
    (handle.layout.project_dir / "facts.json").symlink_to(external)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))

    with pytest.raises(
        ProjectTransactionConflictError,
        match="symbolic link",
    ):
        tx.commit()


def test_requirement_history_is_not_rewritten_by_snapshot_transaction(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    requirements = RequirementVersionStore(projects)
    first = requirements.create_initial(handle.project_id, ["A"])

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.stage_assumptions(assumption_set(handle.project_id))
    tx.commit()

    assert requirements.open_latest(handle.project_id) == first
    assert requirements.open_version(handle.project_id, 1) == first


def test_transaction_does_not_change_req_sequence(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    with sqlite3.connect(projects.database_path) as connection:
        before = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences "
            "WHERE kind='requirement'"
        ).fetchone()[0]

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_decisions(decision_set(handle.project_id))
    tx.commit()

    with sqlite3.connect(projects.database_path) as connection:
        after = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences "
            "WHERE kind='requirement'"
        ).fetchone()[0]

    assert after == before


def test_s3_9_does_not_change_sqlite_schema(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    tx = ProjectStateTransaction(projects, handle.project_id)
    tx.stage_facts(fact_set(handle.project_id))
    tx.commit()

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


def test_transaction_has_no_arbitrary_file_or_sql_mutation_api(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tx = ProjectStateTransaction(projects, handle.project_id)

    for name in (
        "stage_file",
        "replace_file",
        "execute",
        "execute_sql",
        "connection",
        "cursor",
        "stage_requirement_version",
    ):
        assert not hasattr(tx, name)


def test_transaction_module_has_no_network_ai_or_uav_runtime_imports() -> None:
    text = Path(transaction_module.__file__).read_text(
        encoding="utf-8"
    ).lower()

    for forbidden in (
        "ollama",
        "requests",
        "httpx",
        "mavsdk",
        "px4",
        "gazebo",
    ):
        assert forbidden not in text


def test_transaction_module_has_no_retry_or_sleep_loop() -> None:
    text = Path(transaction_module.__file__).read_text(
        encoding="utf-8"
    )

    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
