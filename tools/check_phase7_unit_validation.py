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
from ai.validators import DeterministicUnitValidator


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-v7-3-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Validate deterministic engineering units",
                role="uav_systems_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(),
                output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
                validators=("schema", "finite_values", "units"),
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
                            "statement": "Use deterministic unit validation.",
                        }
                    ],
                    "assumptions": [],
                    "calculations": [
                        {
                            "mass": {"value": 2.1, "unit": "kg"},
                            "battery_energy": {"value": 60.0, "unit": "Wh"},
                            "reserve_percent": 20.0,
                            "gravity_m_s2": 9.81,
                        }
                    ],
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
            created_timestamp="2026-10-05T12:30:00Z",
        )

        validator = DeterministicUnitValidator(projects)
        valid = validator.validate(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert valid.overall is ValidationStatus.PASS
        assert valid.checks[0].check_id == "units"

        def raw_artifact(content: bytes):
            return ArtifactVersionStore(projects).create_artifact(
                handle.project_id,
                ArtifactVersionInput(
                    type=draft.record.type,
                    producer_task=task.task_id,
                    input_dependency_versions=(),
                    content=content,
                    created_timestamp="2026-10-05T12:31:00Z",
                ),
            )

        unknown = raw_artifact(
            b'{"mass":{"value":2.1,"unit":"lb"}}\n'
        )
        unknown_result = validator.validate(
            handle.project_id, unknown.artifact_id, unknown.version
        )
        assert unknown_result.overall is ValidationStatus.FAIL

        mismatch = raw_artifact(
            b'{"mass_kg":{"value":2100,"unit":"g"}}\n'
        )
        mismatch_result = validator.validate(
            handle.project_id, mismatch.artifact_id, mismatch.version
        )
        assert mismatch_result.overall is ValidationStatus.FAIL

        suffix_only = raw_artifact(
            b'{"mass_kg":2.1,"capacity_wh":60,"reserve_percent":20}\n'
        )
        suffix_result = validator.validate(
            handle.project_id, suffix_only.artifact_id, suffix_only.version
        )
        assert suffix_result.overall is ValidationStatus.PASS

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert reopened.status is ArtifactState.DRAFT
        assert reopened.validation_result is None
        assert tasks.open_task(handle.project_id, task.task_id) == before_task

        restarted = ProjectStore(projects.projects_root)
        restarted_result = DeterministicUnitValidator(restarted).validate(
            handle.project_id, draft.record.artifact_id, draft.record.version
        )
        assert restarted_result == valid

        print(
            "UNIT_VALIDATION=PASS "
            f"artifact={draft.record.artifact_id.value}@v{draft.record.version} "
            "check=units "
            "status=PASS "
            "unknown_unit=FAIL "
            "suffix_mismatch=FAIL "
            "suffix_only=PASS "
            "restart=PASS "
            "artifact_state=DRAFT "
            "persisted_validation=none "
            "task_state=unchanged "
            "provider_calls=none "
            "mass_budget=reserved_for_V7_4 "
            "lifecycle_transition=reserved "
            "cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
