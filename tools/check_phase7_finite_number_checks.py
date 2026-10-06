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
from ai.validators import DeterministicFiniteNumberValidator


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-v7-2-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Validate finite engineering numbers",
                role="uav_systems_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(),
                output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
                validators=("schema", "finite_values"),
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
                            "statement": "Use deterministic finite-number validation.",
                        }
                    ],
                    "assumptions": [],
                    "calculations": [{"mass_kg": 2.1, "rotors": 4}],
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
            created_timestamp="2026-10-05T10:30:00Z",
        )

        validator = DeterministicFiniteNumberValidator(projects)
        valid = validator.validate(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert valid.overall is ValidationStatus.PASS
        assert valid.checks[0].check_id == "finite_values"

        def raw_artifact(content: bytes):
            return ArtifactVersionStore(projects).create_artifact(
                handle.project_id,
                ArtifactVersionInput(
                    type=draft.record.type,
                    producer_task=task.task_id,
                    input_dependency_versions=(),
                    content=content,
                    created_timestamp="2026-10-05T10:31:00Z",
                ),
            )

        nonfinite = validator.validate(
            handle.project_id,
            raw_artifact(b'{"value":NaN}\n').artifact_id,
            1,
        )
        assert nonfinite.overall is ValidationStatus.FAIL

        overflow = validator.validate(
            handle.project_id,
            raw_artifact(b'{"value":1e309}\n').artifact_id,
            1,
        )
        assert overflow.overall is ValidationStatus.FAIL

        huge = validator.validate(
            handle.project_id,
            raw_artifact((b'{"value":' + (b"9" * 400) + b'}\n')).artifact_id,
            1,
        )
        assert huge.overall is ValidationStatus.FAIL

        nested = validator.validate(
            handle.project_id,
            raw_artifact(b'{"x":[{"y":-1e309}]}\n').artifact_id,
            1,
        )
        assert nested.overall is ValidationStatus.FAIL

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert reopened.status is ArtifactState.DRAFT
        assert reopened.validation_result is None
        assert tasks.open_task(handle.project_id, task.task_id) == before_task

        restarted = ProjectStore(projects.projects_root)
        restarted_result = DeterministicFiniteNumberValidator(restarted).validate(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert restarted_result == valid

        print(
            "FINITE_NUMBER_CHECK=PASS "
            f"artifact={draft.record.artifact_id.value}@v{draft.record.version} "
            "check=finite_values "
            "status=PASS "
            "nonfinite_constant=FAIL "
            "overflow=FAIL "
            "huge_integer=FAIL "
            "nested=FAIL "
            "restart=PASS "
            "artifact_state=DRAFT "
            "persisted_validation=none "
            "task_state=unchanged "
            "provider_calls=none "
            "units=reserved_for_V7_3 "
            "lifecycle_transition=reserved "
            "cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
