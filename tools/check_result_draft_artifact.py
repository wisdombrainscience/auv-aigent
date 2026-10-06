#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import ARTIFACT_REGISTRY_DB_FILENAME, ArtifactInputVersion
from ai.contracts.artifact_states import ArtifactState
from ai.project.requirement_versions import RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore


def _tables(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-g6-8-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()

        requirement = RequirementVersionStore(projects).create_initial(
            handle.project_id,
            ["Payload shall remain below 2 kg."],
            status=RequirementSetStatus.APPROVED,
        )
        req_id = requirement.requirement_set.requirements[0].requirement_id

        tasks = TaskStore(projects)
        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Create one bounded engineering draft artifact",
                role="uav_systems_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(
                    ArtifactInputVersion(req_id, requirement.version),
                ),
                output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
                validators=("schema",),
                review_required=False,
                max_attempts=3,
            ),
        )
        before_task = tasks.open_task(handle.project_id, task.task_id)

        context = MinimalContextBuilder(projects).build(
            handle.project_id, task.task_id
        )
        payload = {
            "status": "PROPOSED",
            "claims": [
                {
                    "classification": "AI_PROPOSAL",
                    "statement": "Use the bounded engineering proposal.",
                }
            ],
            "assumptions": [],
            "calculations": [{"mass_kg": 2.1}],
            "artifacts": [
                {
                    "kind": "design_note",
                    "state": "APPROVED",
                    "note": "Nested model content has no lifecycle authority.",
                }
            ],
            "risks": [],
            "missing_information": [],
            "open_questions": [],
        }
        response = GenerateResponse(
            model="local-acceptance-fixture",
            content=json.dumps(payload, separators=(",", ":")),
            done=True,
        )
        output = SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id, task.task_id, response
        )
        run = AgentRunStore(projects).create_run(output)
        manifest = ContextManifestStore(projects).create_manifest(run, context)

        store = ResultDraftArtifactStore(projects)
        created = store.create_artifact(
            handle.project_id,
            run.run_id,
            created_timestamp="2026-10-05T01:30:00Z",
        )
        repeated = store.create_artifact(
            handle.project_id,
            run.run_id,
            created_timestamp="2026-10-05T01:31:00Z",
        )

        assert repeated == created
        assert created.record.artifact_id.value == "ART-000001"
        assert created.record.version == 1
        assert created.record.status is ArtifactState.DRAFT
        assert created.record.validation_result is None
        assert created.record.input_dependency_versions == task.input_artifacts
        assert created.payload.run_id == run.run_id
        assert created.payload.context_manifest_sha256 == manifest.manifest_sha256
        assert created.payload.context_sha256 == manifest.context_sha256
        assert created.payload.result.to_dict()["status"] == "PROPOSED"
        assert created.payload.result.to_dict()["artifacts"][0]["state"] == "APPROVED"
        assert tasks.open_task(handle.project_id, task.task_id) == before_task

        restarted = ProjectStore(projects.projects_root)
        reopened = ResultDraftArtifactStore(restarted).open_artifact(
            handle.project_id,
            created.record.artifact_id,
            created.record.version,
        )
        assert reopened == created

        run_registry = (
            handle.layout.data_directories["ai_runs"]
            / "agent_run_registry.sqlite3"
        )
        artifact_registry = (
            handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
        )
        assert _tables(run_registry) == (
            "agent_run_registry_meta",
            "agent_runs",
        )
        assert len(_tables(artifact_registry)) == 6

        print(
            "RESULT_DRAFT_ARTIFACT=PASS "
            f"run={run.run_id.value} "
            f"artifact={created.record.artifact_id.value}@v{created.record.version} "
            "status=DRAFT "
            "proposal=PASS "
            "lineage=PASS "
            "run_binding=PASS "
            "manifest_binding=PASS "
            "idempotent=PASS "
            "restart=PASS "
            "artifact_registry_tables=6 "
            "run_registry_tables=2 "
            "task_state=unchanged "
            "provider_calls=none "
            "review=reserved "
            "validation=reserved_for_phase7 "
            "approval=reserved "
            "cp6=READY"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
