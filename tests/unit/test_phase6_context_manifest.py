from __future__ import annotations
import json
from pathlib import Path
import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID, CONTEXT_MANIFEST_CONTRACT_VERSION,
    CONTEXT_MANIFEST_DIRNAME, MAX_CONTEXT_MANIFEST_BYTES,
    AgentRunStore, ContextManifest, ContextManifestConflictError,
    ContextManifestNotFoundError, ContextManifestStore, MinimalContextBuilder,
    SchemaEnforcedOutputValidator,
)
from ai.project.facts import Fact, FactSet, FactStore
from ai.contracts.provenance import ProvenanceClass
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore

def task_input():
    return TaskCreateInput(title="Manifest task",role="uav_systems_engineer",priority=100,
        dependencies=(),input_artifacts=(),output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema",),review_required=False,max_attempts=3)

def response():
    payload={"status":"PROPOSED","claims":[{"classification":"AI_PROPOSAL","statement":"bounded"}],
             "assumptions":[],"calculations":[],"artifacts":[],"risks":[],
             "missing_information":[],"open_questions":[]}
    return GenerateResponse(model="local-test",content=json.dumps(payload,separators=(",",":")),done=True)

def setup(tmp_path, *, facts=False):
    projects=ProjectStore(tmp_path/"remote-uav"/"projects")
    handle=projects.create_project()
    task=TaskStore(projects).create_task(handle.project_id,task_input())
    if facts:
        FactStore(projects).save(FactSet(project_id=handle.project_id,facts=(
            Fact("vehicle.mass_kg",2.1,ProvenanceClass.USER_FACT),
            Fact("mission.endurance_min",20,ProvenanceClass.VERIFIED_FACT),
        )))
    context=MinimalContextBuilder(projects).build(handle.project_id,task.task_id,
        fact_keys=("mission.endurance_min","vehicle.mass_kg") if facts else ())
    output=SchemaEnforcedOutputValidator(projects).enforce(handle.project_id,task.task_id,response())
    run=AgentRunStore(projects).create_run(output)
    return projects,handle,task,context,run

def test_contract_constants():
    assert CONTEXT_MANIFEST_CONTRACT_VERSION=="1.0"
    assert CONTEXT_MANIFEST_DIRNAME=="context_manifests"
    assert MAX_CONTEXT_MANIFEST_BYTES==512*1024

def test_create_manifest_binds_run_and_context(tmp_path):
    projects,handle,task,context,run=setup(tmp_path)
    m=ContextManifestStore(projects).create_manifest(run,context)
    assert m.run_id==run.run_id.value and m.task_id==task.task_id.value
    assert m.project_id==handle.project_id.value
    assert len(m.context_sha256)==64 and len(m.manifest_sha256)==64

def test_manifest_has_no_artifact_or_approval_fields(tmp_path):
    projects,_,_,context,run=setup(tmp_path)
    raw=ContextManifestStore(projects).create_manifest(run,context).to_dict()
    assert "artifact_id" not in raw and "approval" not in raw and "verdict" not in raw
    assert "provider" not in raw and "prompt" not in raw

def test_restart_round_trip(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    first=ContextManifestStore(projects).create_manifest(run,context)
    reopened=ContextManifestStore(ProjectStore(projects.projects_root)).open_manifest(handle.project_id,run.run_id)
    assert reopened==first

def test_fact_keys_are_recorded_canonically(tmp_path):
    projects,_,_,context,run=setup(tmp_path,facts=True)
    m=ContextManifestStore(projects).create_manifest(run,context)
    assert m.fact_keys==("mission.endurance_min","vehicle.mass_kg")

def test_create_is_idempotent_for_same_manifest(tmp_path):
    projects,_,_,context,run=setup(tmp_path)
    store=ContextManifestStore(projects)
    assert store.create_manifest(run,context)==store.create_manifest(run,context)

def test_missing_manifest_is_not_found(tmp_path):
    projects,handle,_,_,run=setup(tmp_path)
    with pytest.raises(ContextManifestNotFoundError):
        ContextManifestStore(projects).open_manifest(handle.project_id,run.run_id)

def test_wrong_context_task_rejected(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    other=TaskStore(projects).create_task(handle.project_id,task_input())
    other_context=MinimalContextBuilder(projects).build(handle.project_id,other.task_id)
    with pytest.raises(ContextManifestConflictError,match="identity"):
        ContextManifestStore(projects).create_manifest(run,other_context)

def test_tampered_manifest_hash_rejected(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    store=ContextManifestStore(projects); store.create_manifest(run,context)
    path=handle.layout.data_directories["ai_runs"]/CONTEXT_MANIFEST_DIRNAME/f"{run.run_id.value}.json"
    data=json.loads(path.read_text()); data["context_sha256"]="0"*64
    path.write_text(json.dumps(data))
    with pytest.raises(ContextManifestConflictError,match="hash mismatch"):
        store.open_manifest(handle.project_id,run.run_id)

def test_duplicate_json_key_rejected(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    store=ContextManifestStore(projects); m=store.create_manifest(run,context)
    path=handle.layout.data_directories["ai_runs"]/CONTEXT_MANIFEST_DIRNAME/f"{run.run_id.value}.json"
    raw=json.dumps(m.to_dict(),separators=(",",":"))
    path.write_text(raw[:-1]+',"run_id":"AI-RUN-999999"}')
    with pytest.raises(ContextManifestConflictError,match="duplicate"):
        store.open_manifest(handle.project_id,run.run_id)

def test_symlink_manifest_path_rejected(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    store=ContextManifestStore(projects); store.create_manifest(run,context)
    path=handle.layout.data_directories["ai_runs"]/CONTEXT_MANIFEST_DIRNAME/f"{run.run_id.value}.json"
    target=path.with_suffix(".real"); path.rename(target); path.symlink_to(target)
    with pytest.raises(ContextManifestConflictError,match="unsafe"):
        store.open_manifest(handle.project_id,run.run_id)

def test_context_change_after_manifest_fails_closed(tmp_path):
    projects,handle,_,context,run=setup(tmp_path,facts=True)
    store=ContextManifestStore(projects); store.create_manifest(run,context)
    FactStore(projects).save(FactSet(project_id=handle.project_id,facts=(
        Fact("vehicle.mass_kg",2.2,ProvenanceClass.USER_FACT),
        Fact("mission.endurance_min",20,ProvenanceClass.VERIFIED_FACT),
    )))
    with pytest.raises(ContextManifestConflictError,match="changed"):
        store.open_manifest(handle.project_id,run.run_id)

def test_task_state_unchanged(tmp_path):
    projects,handle,task,context,run=setup(tmp_path)
    tasks=TaskStore(projects); before=tasks.open_task(handle.project_id,task.task_id)
    ContextManifestStore(projects).create_manifest(run,context)
    assert tasks.open_task(handle.project_id,task.task_id)==before

def test_run_record_unchanged(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    runs=AgentRunStore(projects)
    before=runs.open_run(handle.project_id,run.run_id)
    ContextManifestStore(projects).create_manifest(run,context)
    assert runs.open_run(handle.project_id,run.run_id)==before

def test_manifest_from_dict_rejects_extra_fields(tmp_path):
    projects,_,_,context,run=setup(tmp_path)
    m=ContextManifestStore(projects).create_manifest(run,context)
    with pytest.raises(ContextManifestConflictError,match="exactly"):
        ContextManifest.from_dict({**m.to_dict(),"artifact_id":"ART-000001"})

def test_manifest_from_dict_rejects_unsorted_fact_keys(tmp_path):
    projects,_,_,context,run=setup(tmp_path,facts=True)
    m=ContextManifestStore(projects).create_manifest(run,context).to_dict()
    m["fact_keys"]=["vehicle.mass_kg","mission.endurance_min"]
    with pytest.raises(ContextManifestConflictError,match="canonical"):
        ContextManifest.from_dict(m)

def test_manifest_file_is_under_ai_runs_only(tmp_path):
    projects,handle,_,context,run=setup(tmp_path)
    ContextManifestStore(projects).create_manifest(run,context)
    path=handle.layout.data_directories["ai_runs"]/CONTEXT_MANIFEST_DIRNAME/f"{run.run_id.value}.json"
    assert path.is_file()
    assert path.parent.parent==handle.layout.data_directories["ai_runs"]

def test_no_new_sqlite_table_created(tmp_path):
    import sqlite3
    projects,handle,_,context,run=setup(tmp_path)
    ContextManifestStore(projects).create_manifest(run,context)
    reg=handle.layout.data_directories["ai_runs"]/"agent_run_registry.sqlite3"
    with sqlite3.connect(reg) as c:
        tables=tuple(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
    assert tables==("agent_run_registry_meta","agent_runs")

def test_store_exposes_no_artifact_provider_or_uav_api(tmp_path):
    projects,_,_,_,_=setup(tmp_path)
    store=ContextManifestStore(projects)
    for name in ("generate","execute","provider","retry","create_artifact","approve","review","validate","command_uav"):
        assert not hasattr(store,name)

def test_manifest_cannot_be_created_from_non_run(tmp_path):
    projects,_,_,context,_=setup(tmp_path)
    with pytest.raises(TypeError):
        ContextManifestStore(projects).create_manifest("AI-RUN-000001",context)

def test_context_contract_version_bound(tmp_path):
    projects,_,_,context,run=setup(tmp_path)
    m=ContextManifestStore(projects).create_manifest(run,context)
    assert m.context_contract_version==context.to_dict()["contract_version"]
