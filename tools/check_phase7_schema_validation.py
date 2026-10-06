#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

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
from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import DeterministicSchemaValidator


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-v7-1-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Validate deterministic artifact schema",
                role="uav_systems_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(),
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
        response = GenerateResponse(
            model="local-acceptance-fixture",
            content=json.dumps(
                {
                    "status": "PROPOSED",
                    "claims": [
                        {
                            "classification": "AI_PROPOSAL",
                            "statement": "Use deterministic schema validation.",
                        }
                    ],
                    "assumptions": [],
                    "calculations": [{"mass_kg": 2.1}],
                    "artifacts": [],
                    "risks": [],
                    "missing_information": [],
                    "open_questions": [],
                },
                separators=(",", ":"),
            ),
            done=True,
        )
        output = SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id, task.task_id, response
        )
        run = AgentRunStore(projects).create_run(output)
        ContextManifestStore(projects).create_manifest(run, context)
        draft = ResultDraftArtifactStore(projects).create_artifact(
            handle.project_id,
            run.run_id,
            created_timestamp="2026-10-05T09:30:00Z",
        )

        validator = DeterministicSchemaValidator(projects)
        valid = validator.validate(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert valid.overall is ValidationStatus.PASS
        assert valid.checks[0].check_id == "schema"

        invalid_record = ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type=draft.record.type,
                producer_task=task.task_id,
                input_dependency_versions=(),
                content=b"{}\n",
                created_timestamp="2026-10-05T09:31:00Z",
            ),
        )
        invalid = validator.validate(
            handle.project_id, invalid_record.artifact_id, invalid_record.version
        )
        assert invalid.overall is ValidationStatus.FAIL

        unknown_record = ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="engineering.unknown.v1",
                producer_task=task.task_id,
                input_dependency_versions=(),
                content=b"{}\n",
                created_timestamp="2026-10-05T09:32:00Z",
            ),
        )
        unknown = validator.validate(
            handle.project_id, unknown_record.artifact_id, unknown_record.version
        )
        assert unknown.overall is ValidationStatus.FAIL

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert reopened.status is ArtifactState.DRAFT
        assert reopened.validation_result is None
        assert tasks.open_task(handle.project_id, task.task_id) == before_task

        restarted = ProjectStore(projects.projects_root)
        restarted_result = DeterministicSchemaValidator(restarted).validate(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert restarted_result == valid

        print(
            "SCHEMA_VALIDATION=PASS "
            f"artifact={draft.record.artifact_id.value}@v{draft.record.version} "
            "check=schema "
            "status=PASS "
            "invalid=FAIL "
            "unknown_type=FAIL "
            "restart=PASS "
            "artifact_state=DRAFT "
            "persisted_validation=none "
            "task_state=unchanged "
            "provider_calls=none "
            "finite_numbers=reserved_for_V7_2 "
            "units=reserved_for_V7_3 "
            "lifecycle_transition=reserved "
            "cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
