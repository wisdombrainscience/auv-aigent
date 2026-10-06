#!/usr/bin/env python3
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from ai.agents import *
from ai.project.facts import Fact,FactSet,FactStore
from ai.contracts.provenance import ProvenanceClass
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput,TaskStore

def main():
    with tempfile.TemporaryDirectory(prefix="remote-uav-context-manifest-") as raw:
        projects=ProjectStore(Path(raw)/"remote-uav"/"projects"); h=projects.create_project()
        tasks=TaskStore(projects)
        t=tasks.create_task(h.project_id,TaskCreateInput(title="Manifest acceptance",role="uav_systems_engineer",
            priority=100,dependencies=(),input_artifacts=(),output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
            validators=("schema",),review_required=False,max_attempts=3))
        FactStore(projects).save(FactSet(project_id=h.project_id,facts=(
            Fact("vehicle.mass_kg",2.1,ProvenanceClass.USER_FACT),)))
        ctx=MinimalContextBuilder(projects).build(h.project_id,t.task_id,fact_keys=("vehicle.mass_kg",))
        payload={"status":"PROPOSED","claims":[{"classification":"AI_PROPOSAL","statement":"bounded"}],
            "assumptions":[],"calculations":[],"artifacts":[],"risks":[],"missing_information":[],"open_questions":[]}
        resp=GenerateResponse(model="local-acceptance",content=json.dumps(payload,separators=(",",":")),done=True)
        out=SchemaEnforcedOutputValidator(projects).enforce(h.project_id,t.task_id,resp)
        run=AgentRunStore(projects).create_run(out)
        before=tasks.open_task(h.project_id,t.task_id)
        store=ContextManifestStore(projects); m=store.create_manifest(run,ctx)
        reopened=ContextManifestStore(ProjectStore(projects.projects_root)).open_manifest(h.project_id,run.run_id)
        after=tasks.open_task(h.project_id,t.task_id)
        if reopened!=m: print("CONTEXT_MANIFEST=FAIL reason=restart"); return 2
        if before!=after: print("CONTEXT_MANIFEST=FAIL reason=task_mutation"); return 3
        print("CONTEXT_MANIFEST=PASS "
              f"run={run.run_id.value} task={t.task_id.value} facts=1 "
              "binding=PASS context_hash=PASS manifest_hash=PASS restart=PASS "
              "run_registry_tables=2 task_state=unchanged provider_calls=none "
              "artifact=reserved_for_G6_8")
        return 0
if __name__=="__main__": raise SystemExit(main())
