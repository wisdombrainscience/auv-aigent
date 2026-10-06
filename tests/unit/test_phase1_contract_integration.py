from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import ai.contracts as contracts
from ai.contracts.agent_result import AgentResult, AgentResultError
from ai.contracts.artifact_states import (
    ArtifactState,
    InvalidArtifactStateTransition,
    require_artifact_transition,
)
from ai.contracts.failures import FailureDescriptor, FailureTaxonomyError
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.provenance import ProvenanceClass, ProvenanceError
from ai.contracts.review_result import ReviewResult, ReviewResultError
from ai.contracts.task_states import (
    InvalidTaskStateTransition,
    TaskState,
    recover_task_state_after_restart,
    require_task_transition,
)
from ai.contracts.validation_result import (
    ValidationResult,
    ValidationResultError,
)


CONTRACT_MODULES = [
    "ids.py",
    "task_states.py",
    "artifact_states.py",
    "provenance.py",
    "agent_result.py",
    "review_result.py",
    "validation_result.py",
    "failures.py",
]


def json_round_trip(payload: dict[str, object]) -> dict[str, object]:
    return json.loads(
        json.dumps(
            payload,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def test_all_phase1_contract_versions_are_explicit_and_stable() -> None:
    assert contracts.ID_CONTRACT_VERSION == "1.0"
    assert contracts.TASK_STATE_CONTRACT_VERSION == "1.0"
    assert contracts.ARTIFACT_STATE_CONTRACT_VERSION == "1.0"
    assert contracts.PROVENANCE_CONTRACT_VERSION == "1.0"
    assert contracts.AGENT_RESULT_CONTRACT_VERSION == "1.0"
    assert contracts.REVIEW_RESULT_CONTRACT_VERSION == "1.0"
    assert contracts.VALIDATION_RESULT_CONTRACT_VERSION == "1.0"
    assert contracts.FAILURE_TAXONOMY_CONTRACT_VERSION == "1.0"


def test_all_phase1_public_contracts_are_exported_from_package() -> None:
    expected = {
        "CanonicalId",
        "IdKind",
        "TaskState",
        "ArtifactState",
        "ProvenanceClass",
        "AgentResult",
        "ReviewResult",
        "ValidationResult",
        "FailureDescriptor",
    }
    for name in expected:
        assert hasattr(contracts, name), name
        assert name in contracts.__all__


def test_representative_engineering_chain_round_trips_as_strict_json() -> None:
    agent_payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "USER_REQUIREMENT",
                "statement": "Endurance must be at least 30 minutes.",
            },
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Estimated usable energy.",
                "value_wh": 180.0,
            },
        ],
        "assumptions": [
            {
                "classification": "ENGINEERING_ASSUMPTION",
                "name": "air_density_kg_m3",
                "value": 1.225,
            }
        ],
        "calculations": [
            {
                "name": "energy_budget",
                "result_wh": 180.0,
            }
        ],
        "artifacts": [
            {
                "artifact_id": "ART-000001",
                "state": "DRAFT",
                "kind": "energy_budget",
            }
        ],
        "risks": [
            {
                "category": "ENERGY",
                "severity": "HIGH",
            }
        ],
        "missing_information": [],
        "open_questions": [
            "What battery reserve percentage is required?"
        ],
    }
    agent_result = AgentResult.from_dict(agent_payload)
    assert json_round_trip(agent_result.to_dict()) == agent_payload

    review_payload = {
        "issues": [
            {
                "issue_id": "ISSUE-0041",
                "fingerprint": "energy:reserve:requirement",
                "severity": "HIGH",
                "category": "ENERGY",
                "field": "battery.reserve_percent",
                "reason": "Reserve requirement is not specified.",
                "required_action": "Request the required reserve percentage.",
            }
        ]
    }
    review_result = ReviewResult.from_dict(review_payload)
    assert json_round_trip(review_result.to_dict()) == review_payload

    validation_payload = {
        "overall": "FAIL",
        "checks": [
            {
                "check_id": "battery_reserve",
                "status": "FAIL",
                "field": "battery.reserve_percent",
                "reason": "Required reserve threshold is unavailable.",
                "details": {
                    "observed_percent": 18.0,
                    "required_min_percent": None,
                },
            }
        ],
    }
    validation_result = ValidationResult.from_dict(validation_payload)
    assert json_round_trip(validation_result.to_dict()) == validation_payload

    failure_payload = {
        "class": "VALIDATION",
        "code": "VALIDATION_FAILED",
        "message": "Deterministic engineering validation failed.",
        "details": {
            "failed_check_ids": ["battery_reserve"],
        },
    }
    failure = FailureDescriptor.from_dict(failure_payload)
    assert json_round_trip(failure.to_dict()) == failure_payload


def test_agent_proposal_never_promotes_artifact_state() -> None:
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Use a larger battery.",
            }
        ],
        "assumptions": [],
        "calculations": [],
        "artifacts": [
            {
                "artifact_id": "ART-000001",
                "state": "DRAFT",
            }
        ],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    result = AgentResult.from_dict(payload)
    assert result.status.value == "PROPOSED"
    assert result.to_dict()["artifacts"][0]["state"] == "DRAFT"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("CREATED", "DONE"),
        ("READY", "RUNNING"),
        ("RUNNING", "DONE"),
        ("DONE", "READY"),
    ],
)
def test_invalid_task_state_shortcuts_are_rejected(
    current: str,
    target: str,
) -> None:
    with pytest.raises(InvalidTaskStateTransition):
        require_task_transition(current, target)


def test_restart_rule_does_not_resume_running_task_directly() -> None:
    assert (
        recover_task_state_after_restart(TaskState.RUNNING)
        is TaskState.INTERRUPTED
    )
    with pytest.raises(InvalidTaskStateTransition):
        require_task_transition(TaskState.INTERRUPTED, TaskState.RUNNING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DRAFT", "APPROVED"),
        ("REVIEWED", "APPROVED"),
        ("STALE", "DRAFT"),
        ("SUPERSEDED", "APPROVED"),
    ],
)
def test_invalid_artifact_state_shortcuts_are_rejected(
    current: str,
    target: str,
) -> None:
    with pytest.raises(InvalidArtifactStateTransition):
        require_artifact_transition(current, target)


@pytest.mark.parametrize(
    "bad",
    [
        "user_fact",
        "USER FACT",
        "VERIFIED-FACT",
        "AI proposal",
    ],
)
def test_provenance_aliases_and_normalization_are_rejected(bad: str) -> None:
    with pytest.raises(ProvenanceError):
        contracts.parse_provenance_class(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "TASK-1",
        "ART-000000",
        "ISSUE-0000",
        " task-000001",
        "REQ 000001",
    ],
)
def test_cross_contract_ids_remain_strict(bad: str) -> None:
    with pytest.raises(CanonicalIdError):
        CanonicalId.parse(bad)


def test_review_cannot_embed_artifact_mutation_or_verdict() -> None:
    for extra in (
        {"artifact_patch": {}},
        {"replacement_artifact": {}},
        {"verdict": "PASS"},
        {"status": "APPROVED"},
    ):
        payload: dict[str, object] = {"issues": []}
        payload.update(extra)
        with pytest.raises(ReviewResultError):
            ReviewResult.from_dict(payload)


def test_validation_cannot_hide_failed_child_check() -> None:
    payload = {
        "overall": "PASS",
        "checks": [
            {
                "check_id": "energy",
                "status": "FAIL",
                "field": "battery.capacity_wh",
                "reason": "Insufficient energy.",
                "details": {},
            }
        ],
    }
    with pytest.raises(ValidationResultError):
        ValidationResult.from_dict(payload)


def test_failure_record_cannot_authorize_retry_or_task_transition() -> None:
    base: dict[str, object] = {
        "class": "MODEL",
        "code": "MODEL_INVALID_OUTPUT",
        "message": "Structured model output is invalid.",
        "details": {},
    }
    for extra in (
        {"retry": True},
        {"retry_count": 1},
        {"next_state": "READY"},
        {"automatic_action": "RETRY"},
    ):
        payload = dict(base)
        payload.update(extra)
        with pytest.raises(FailureTaxonomyError):
            FailureDescriptor.from_dict(payload)


def test_agent_cannot_claim_final_authority() -> None:
    base = {
        "claims": [],
        "assumptions": [],
        "calculations": [],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    for status in ("PASS", "APPROVED", "DONE", "FAILED"):
        with pytest.raises(AgentResultError):
            AgentResult.from_dict(
                {
                    "status": status,
                    **base,
                }
            )


def test_failure_domains_keep_simulation_and_verification_separate() -> None:
    simulation = FailureDescriptor.from_dict(
        {
            "class": "SIMULATION",
            "code": "SIMULATION_FAILED",
            "message": "Simulation execution failed.",
            "details": {},
        }
    )
    verification = FailureDescriptor.from_dict(
        {
            "class": "VERIFICATION",
            "code": "VERIFICATION_FAILED",
            "message": "Independent verification failed.",
            "details": {},
        }
    )
    assert simulation.failure_class is not verification.failure_class


def test_contract_modules_do_not_import_uav_execution_plane() -> None:
    contracts_dir = Path(contracts.__file__).resolve().parent
    forbidden_roots = {
        "mavsdk",
        "orchestration",
        "src",
    }
    forbidden_fragments = {
        "px4",
        "gazebo",
        "mission_experiment",
        "capability",
    }

    for filename in CONTRACT_MODULES:
        path = contracts_dir / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue

            for name in names:
                root = name.split(".", 1)[0]
                assert root not in forbidden_roots, (filename, name)
                lowered = name.lower()
                assert not any(
                    fragment in lowered
                    for fragment in forbidden_fragments
                ), (filename, name)


def test_phase1_contract_layer_has_no_retry_loop_primitive() -> None:
    contracts_dir = Path(contracts.__file__).resolve().parent
    for filename in CONTRACT_MODULES:
        text = (contracts_dir / filename).read_text(encoding="utf-8")
        assert "while True" not in text
        assert "asyncio.sleep" not in text
        assert "time.sleep" not in text


def test_canonical_id_kinds_cover_phase1_references() -> None:
    required = {
        IdKind.PROJECT,
        IdKind.REQUIREMENT,
        IdKind.TASK,
        IdKind.ARTIFACT,
        IdKind.AGENT_RUN,
        IdKind.REVIEW,
        IdKind.VALIDATION,
        IdKind.ISSUE,
    }
    assert set(IdKind) == required


def test_provenance_set_remains_exact() -> None:
    assert {item.value for item in ProvenanceClass} == {
        "USER_FACT",
        "VERIFIED_FACT",
        "USER_REQUIREMENT",
        "ENGINEERING_ASSUMPTION",
        "AI_PROPOSAL",
        "CALCULATED_VALUE",
        "SIMULATION_RESULT",
        "VERIFIER_RESULT",
    }


def test_task_state_set_remains_exact() -> None:
    assert {item.value for item in TaskState} == {
        "CREATED",
        "BLOCKED",
        "READY",
        "QUEUED",
        "ASSIGNED",
        "RUNNING",
        "RESULT_PROPOSED",
        "REVIEWING",
        "VALIDATING",
        "DONE",
        "REWORK",
        "FAILED",
        "INTERRUPTED",
        "NEEDS_HUMAN",
        "CANCELLED",
    }


def test_artifact_state_set_remains_exact() -> None:
    assert {item.value for item in ArtifactState} == {
        "DRAFT",
        "REVIEWED",
        "VALIDATED",
        "APPROVED",
        "REJECTED",
        "STALE",
        "SUPERSEDED",
    }
