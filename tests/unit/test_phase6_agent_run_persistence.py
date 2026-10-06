from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AGENT_RUN_PERSISTENCE_CONTRACT_VERSION,
    AGENT_RUN_REGISTRY_DB_FILENAME,
    AGENT_RUN_REGISTRY_SCHEMA_VERSION,
    MAX_AGENT_RUN_IDENTITY_CHARS,
    MAX_AGENT_RUN_RECORD_JSON_BYTES,
    MAX_AGENT_RUN_ROLE_CHARS,
    MAX_AGENT_RUN_SCHEMA_ID_CHARS,
    AgentRunNotFoundError,
    AgentRunPersistenceConflictError,
    AgentRunRecord,
    AgentRunStore,
    SchemaEnforcedOutputValidator,
    initialize_agent_run_registry,
    verify_agent_run_registry,
)
from ai.contracts.agent_result import AgentResult
from ai.contracts.ids import CanonicalId, IdKind
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore


def task_input(title: str = "Persist agent run") -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def valid_payload(statement: str = "Persist this bounded proposal.") -> dict[str, object]:
    return {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": statement,
            }
        ],
        "assumptions": [],
        "calculations": [],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }


def response(statement: str = "Persist this bounded proposal.") -> GenerateResponse:
    return GenerateResponse(
        model="local-test-model",
        content=json.dumps(valid_payload(statement), separators=(",", ":")),
        done=True,
    )


def make_project(tmp_path: Path, *, title: str = "Persist agent run"):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input(title))
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(title)
    )
    return projects, handle, tasks, task, output


def registry_path(handle) -> Path:
    return handle.layout.data_directories["ai_runs"] / AGENT_RUN_REGISTRY_DB_FILENAME


def project_tables(handle) -> tuple[str, ...]:
    with sqlite3.connect(handle.database_path) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


def test_contract_constants_are_explicit_and_bounded() -> None:
    assert AGENT_RUN_PERSISTENCE_CONTRACT_VERSION == "1.0"
    assert AGENT_RUN_REGISTRY_SCHEMA_VERSION == 1
    assert AGENT_RUN_REGISTRY_DB_FILENAME == "agent_run_registry.sqlite3"
    assert MAX_AGENT_RUN_RECORD_JSON_BYTES == 9 * 1024 * 1024
    assert MAX_AGENT_RUN_ROLE_CHARS == 128
    assert MAX_AGENT_RUN_SCHEMA_ID_CHARS == 128
    assert MAX_AGENT_RUN_IDENTITY_CHARS == 320


def test_create_run_allocates_canonical_ai_run_and_preserves_proposal(tmp_path: Path) -> None:
    projects, handle, _tasks, task, output = make_project(tmp_path)
    record = AgentRunStore(projects).create_run(output)

    assert record.run_id == CanonicalId.from_sequence(IdKind.AGENT_RUN, 1)
    assert record.project_id == handle.project_id
    assert record.task_id == task.task_id
    assert record.role_id == "uav_systems_engineer"
    assert record.agent_identity == f"uav_systems_engineer:{task.task_id.value}"
    assert record.schema_id == AGENT_RESULT_OUTPUT_SCHEMA_ID
    assert record.result == output.result
    assert record.result.to_dict()["status"] == "PROPOSED"


def test_same_schema_enforced_output_may_create_distinct_bounded_run_records(tmp_path: Path) -> None:
    projects, _handle, _tasks, _task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    first = store.create_run(output)
    second = store.create_run(output)
    assert first.run_id.value == "AI-RUN-000001"
    assert second.run_id.value == "AI-RUN-000002"
    assert first.result == second.result


def test_ai_run_ids_are_global_across_projects_root(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first_project = projects.create_project()
    second_project = projects.create_project()
    tasks = TaskStore(projects)
    validator = SchemaEnforcedOutputValidator(projects)
    store = AgentRunStore(projects)

    first_task = tasks.create_task(first_project.project_id, task_input("First"))
    second_task = tasks.create_task(second_project.project_id, task_input("Second"))
    first_output = validator.enforce(first_project.project_id, first_task.task_id, response("First"))
    second_output = validator.enforce(second_project.project_id, second_task.task_id, response("Second"))

    first = store.create_run(first_output)
    second = store.create_run(second_output)
    assert first.run_id.value == "AI-RUN-000001"
    assert second.run_id.value == "AI-RUN-000002"


def test_create_run_updates_existing_project_state_agent_run_sequence(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    record = AgentRunStore(projects).create_run(output)
    with sqlite3.connect(handle.database_path) as connection:
        row = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='agent_run'"
        ).fetchone()
    assert row == (record.run_id.sequence,)


def test_run_registry_is_separate_and_project_schema_stays_exactly_three_tables(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    AgentRunStore(projects).create_run(output)

    assert project_tables(handle) == (
        "canonical_id_sequences",
        "project_schema_meta",
        "projects",
    )
    with sqlite3.connect(registry_path(handle)) as connection:
        assert tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ) == ("agent_run_registry_meta", "agent_runs")
        verify_agent_run_registry(connection)


def test_open_run_returns_exact_immutable_record(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    created = store.create_run(output)
    opened = store.open_run(handle.project_id, created.run_id)
    assert opened == created
    assert opened.to_dict() == created.to_dict()


def test_run_persistence_survives_restart(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    created = AgentRunStore(projects).create_run(output)

    restarted = ProjectStore(projects.projects_root)
    opened = AgentRunStore(restarted).open_run(handle.project_id, created.run_id)
    assert opened == created


def test_run_persistence_does_not_mutate_task_state_or_attempt(tmp_path: Path) -> None:
    projects, handle, tasks, task, output = make_project(tmp_path)
    before = tasks.open_task(handle.project_id, task.task_id)
    AgentRunStore(projects).create_run(output)
    after = tasks.open_task(handle.project_id, task.task_id)
    assert after == before


def test_create_run_requires_schema_enforced_output_type(tmp_path: Path) -> None:
    projects, _handle, _tasks, _task, _output = make_project(tmp_path)
    with pytest.raises(TypeError, match="SchemaEnforcedAgentOutput"):
        AgentRunStore(projects).create_run({"status": "PROPOSED"})  # type: ignore[arg-type]


def test_open_unknown_run_fails_closed(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    store.create_run(output)
    with pytest.raises(AgentRunNotFoundError, match="does not exist"):
        store.open_run(handle.project_id, "AI-RUN-999999")


def test_open_before_registry_exists_fails_closed(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, _output = make_project(tmp_path)
    with pytest.raises(AgentRunNotFoundError, match="registry does not exist"):
        AgentRunStore(projects).open_run(handle.project_id, "AI-RUN-000001")


def test_wrong_run_id_kind_is_rejected(tmp_path: Path) -> None:
    projects, handle, _tasks, task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    store.create_run(output)
    with pytest.raises(AgentRunPersistenceConflictError, match="agent_run"):
        store.open_run(handle.project_id, task.task_id)


def test_tampered_record_json_is_detected(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    record = store.create_run(output)
    path = registry_path(handle)
    with sqlite3.connect(path) as connection:
        payload = record.to_dict()
        payload["role_id"] = "reviewer"
        connection.execute(
            "UPDATE agent_runs SET record_json=? WHERE run_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", record.run_id.value),
        )
        connection.commit()
    with pytest.raises(AgentRunPersistenceConflictError):
        store.open_run(handle.project_id, record.run_id)


def test_tampered_indexed_fields_are_detected(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    record = store.create_run(output)
    with sqlite3.connect(registry_path(handle)) as connection:
        connection.execute(
            "UPDATE agent_runs SET schema_id='review.v1' WHERE run_id=?",
            (record.run_id.value,),
        )
        connection.commit()
    with pytest.raises(AgentRunPersistenceConflictError, match="indexed fields"):
        store.open_run(handle.project_id, record.run_id)


def test_duplicate_json_keys_in_persisted_record_are_rejected(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    store = AgentRunStore(projects)
    record = store.create_run(output)
    original = record.to_dict()
    base = json.dumps(original, sort_keys=True, separators=(",", ":"))
    tampered = base[:-1] + ',"schema_id":"engineering_task.v1"}'
    with sqlite3.connect(registry_path(handle)) as connection:
        connection.execute(
            "UPDATE agent_runs SET record_json=? WHERE run_id=?",
            (tampered, record.run_id.value),
        )
        connection.commit()
    with pytest.raises(AgentRunPersistenceConflictError, match="duplicate JSON key"):
        store.open_run(handle.project_id, record.run_id)


def test_registry_rejects_unexpected_table(tmp_path: Path) -> None:
    path = (tmp_path / "ai_runs" / AGENT_RUN_REGISTRY_DB_FILENAME).resolve()
    initialize_agent_run_registry(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unexpected(x INTEGER)")
        connection.commit()
        with pytest.raises(AgentRunPersistenceConflictError, match="unexpected"):
            verify_agent_run_registry(connection)


def test_registry_path_symlink_is_rejected(tmp_path: Path) -> None:
    real = (tmp_path / "real.sqlite3").resolve()
    with sqlite3.connect(real):
        pass
    link = (tmp_path / "link.sqlite3").resolve()
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(AgentRunPersistenceConflictError, match="symbolic link"):
        initialize_agent_run_registry(link)


def test_failed_insert_does_not_consume_global_ai_run_sequence(tmp_path: Path) -> None:
    projects, handle, _tasks, _task, output = make_project(tmp_path)
    path = registry_path(handle)
    initialize_agent_run_registry(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO agent_runs(run_id,project_id,task_id,role_id,schema_id,record_json) "
            "VALUES(?,?,?,?,?,?)",
            (
                "AI-RUN-000001",
                handle.project_id.value,
                output.binding.task_id.value,
                output.binding.role_id,
                output.schema_id,
                "{}\n",
            ),
        )
        connection.commit()

    store = AgentRunStore(projects)
    with pytest.raises(AgentRunPersistenceConflictError):
        store.create_run(output)

    with sqlite3.connect(handle.database_path) as connection:
        assert connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='agent_run'"
        ).fetchone() == (0,)

    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM agent_runs WHERE run_id='AI-RUN-000001'")
        connection.commit()
    assert store.create_run(output).run_id.value == "AI-RUN-000001"


def test_record_from_dict_requires_exact_fields() -> None:
    result = AgentResult.from_dict(valid_payload())
    base = {
        "run_id": "AI-RUN-000001",
        "project_id": "UAV-PROJ-000001",
        "task_id": "TASK-000001",
        "role_id": "uav_systems_engineer",
        "agent_identity": "uav_systems_engineer:TASK-000001",
        "schema_id": "engineering_task.v1",
        "result": result.to_dict(),
    }
    with pytest.raises(AgentRunPersistenceConflictError, match="exactly"):
        AgentRunRecord.from_dict({**base, "approval": True})
    broken = dict(base)
    broken.pop("schema_id")
    with pytest.raises(AgentRunPersistenceConflictError, match="exactly"):
        AgentRunRecord.from_dict(broken)


def test_record_rejects_identity_not_derived_from_role_and_task() -> None:
    with pytest.raises(AgentRunPersistenceConflictError, match="identity"):
        AgentRunRecord(
            run_id="AI-RUN-000001",
            project_id="UAV-PROJ-000001",
            task_id="TASK-000001",
            role_id="uav_systems_engineer",
            agent_identity="reviewer:TASK-000001",
            schema_id="engineering_task.v1",
            result=AgentResult.from_dict(valid_payload()),
        )


def test_record_remains_proposal_without_approval_or_verdict_fields(tmp_path: Path) -> None:
    projects, _handle, _tasks, _task, output = make_project(tmp_path)
    raw = AgentRunStore(projects).create_run(output).to_dict()
    assert raw["result"]["status"] == "PROPOSED"
    assert "approval" not in raw
    assert "verdict" not in raw
    assert "context_manifest" not in raw
    assert "artifact_id" not in raw


def test_store_exposes_no_manifest_artifact_provider_or_uav_action_api(tmp_path: Path) -> None:
    projects, _handle, _tasks, _task, _output = make_project(tmp_path)
    store = AgentRunStore(projects)
    for name in (
        "generate",
        "execute",
        "provider",
        "retry",
        "create_manifest",
        "context_manifest",
        "create_artifact",
        "approve",
        "review",
        "validate",
        "command_uav",
    ):
        assert not hasattr(store, name)
