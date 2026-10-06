from __future__ import annotations

import json
import math

import pytest

from ai.contracts.agent_result import (
    AGENT_RESULT_CONTRACT_VERSION,
    AgentResult,
    AgentResultError,
    AgentResultStatus,
    parse_agent_result,
)
from ai.contracts.provenance import ProvenanceClass


def minimal_payload() -> dict[str, object]:
    return {
        "status": "PROPOSED",
        "claims": [],
        "assumptions": [],
        "calculations": [],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }


def test_contract_version_is_explicit() -> None:
    assert AGENT_RESULT_CONTRACT_VERSION == "1.0"


def test_minimum_baseline_result_round_trips_exactly() -> None:
    payload = minimal_payload()
    result = AgentResult.from_dict(payload)
    assert result.status is AgentResultStatus.PROPOSED
    assert result.to_dict() == payload
    assert json.loads(json.dumps(result.to_dict())) == payload


def test_parse_helper_returns_agent_result() -> None:
    assert isinstance(parse_agent_result(minimal_payload()), AgentResult)


@pytest.mark.parametrize(
    "status",
    ["DONE", "APPROVED", "PASS", "FAILED", "proposed", " PROPOSED", "PROPOSED "],
)
def test_agent_cannot_claim_final_or_noncanonical_status(status: str) -> None:
    payload = minimal_payload()
    payload["status"] = status
    with pytest.raises(AgentResultError):
        AgentResult.from_dict(payload)


@pytest.mark.parametrize("status", [None, 1, True, [], {}])
def test_status_must_be_string(status: object) -> None:
    payload = minimal_payload()
    payload["status"] = status
    with pytest.raises(AgentResultError):
        AgentResult.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "claims",
        "assumptions",
        "calculations",
        "artifacts",
        "risks",
        "missing_information",
        "open_questions",
    ],
)
def test_every_baseline_field_is_required(field: str) -> None:
    payload = minimal_payload()
    del payload[field]
    with pytest.raises(AgentResultError, match="missing="):
        AgentResult.from_dict(payload)


def test_unknown_top_level_fields_are_rejected() -> None:
    payload = minimal_payload()
    payload["verdict"] = "PASS"
    with pytest.raises(AgentResultError, match="extra=verdict"):
        AgentResult.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    [
        "claims",
        "assumptions",
        "calculations",
        "artifacts",
        "risks",
        "missing_information",
        "open_questions",
    ],
)
@pytest.mark.parametrize("bad", [None, "text", 1, {}, True])
def test_list_fields_reject_non_arrays(field: str, bad: object) -> None:
    payload = minimal_payload()
    payload[field] = bad
    with pytest.raises(AgentResultError):
        AgentResult.from_dict(payload)


@pytest.mark.parametrize(
    "classification",
    [
        "USER_FACT",
        "VERIFIED_FACT",
        "USER_REQUIREMENT",
        "ENGINEERING_ASSUMPTION",
        "AI_PROPOSAL",
        "CALCULATED_VALUE",
        "SIMULATION_RESULT",
        "VERIFIER_RESULT",
    ],
)
def test_claim_requires_canonical_provenance(
    classification: str,
) -> None:
    payload = minimal_payload()
    payload["claims"] = [
        {
            "classification": classification,
            "statement": "example",
        }
    ]
    result = AgentResult.from_dict(payload)
    assert result.to_dict()["claims"][0]["classification"] == classification


def test_provenance_enum_is_serialized_to_wire_value() -> None:
    payload = minimal_payload()
    payload["claims"] = [
        {
            "classification": ProvenanceClass.AI_PROPOSAL,
            "statement": "candidate design",
        }
    ]
    result = AgentResult.from_dict(payload)
    assert result.to_dict()["claims"][0]["classification"] == "AI_PROPOSAL"


@pytest.mark.parametrize(
    "claim",
    [
        "claim text",
        123,
        [],
        {"statement": "missing classification"},
        {"classification": "user_fact", "statement": "bad"},
        {"classification": "UNKNOWN", "statement": "bad"},
    ],
)
def test_malformed_claims_are_rejected(claim: object) -> None:
    payload = minimal_payload()
    payload["claims"] = [claim]
    with pytest.raises(AgentResultError):
        AgentResult.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("missing_information", ""),
        ("missing_information", " "),
        ("missing_information", " leading"),
        ("missing_information", "trailing "),
        ("missing_information", 42),
        ("open_questions", ""),
        ("open_questions", " question"),
        ("open_questions", 42),
    ],
)
def test_human_input_prompts_must_be_clean_strings(
    field: str, bad: object
) -> None:
    payload = minimal_payload()
    payload[field] = [bad]
    with pytest.raises(AgentResultError):
        AgentResult.from_dict(payload)


@pytest.mark.parametrize("bad_number", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize(
    "field",
    ["claims", "assumptions", "calculations", "artifacts", "risks"],
)
def test_nonfinite_numbers_never_enter_agent_result(
    field: str, bad_number: float
) -> None:
    payload = minimal_payload()
    if field == "claims":
        payload[field] = [
            {
                "classification": "CALCULATED_VALUE",
                "value": bad_number,
            }
        ]
    else:
        payload[field] = [{"value": bad_number}]
    with pytest.raises(AgentResultError, match="non-finite"):
        AgentResult.from_dict(payload)


def test_nested_content_must_be_strict_json_compatible() -> None:
    payload = minimal_payload()
    payload["calculations"] = [{"bad": {1, 2, 3}}]
    with pytest.raises(AgentResultError, match="unsupported JSON value type"):
        AgentResult.from_dict(payload)


def test_mapping_keys_must_be_strings() -> None:
    payload = minimal_payload()
    payload["artifacts"] = [{1: "bad"}]
    with pytest.raises(AgentResultError, match="non-string object key"):
        AgentResult.from_dict(payload)


def test_result_owns_immutable_nested_snapshot() -> None:
    claim = {
        "classification": "USER_FACT",
        "statement": "mass supplied by user",
        "meta": {"source": "input"},
    }
    payload = minimal_payload()
    payload["claims"] = [claim]

    result = AgentResult.from_dict(payload)
    claim["statement"] = "mutated after parse"
    claim["meta"]["source"] = "mutated"

    serialized = result.to_dict()
    assert serialized["claims"][0]["statement"] == "mass supplied by user"
    assert serialized["claims"][0]["meta"]["source"] == "input"


def test_serialized_output_is_detached_from_internal_result() -> None:
    payload = minimal_payload()
    payload["assumptions"] = [{"name": "air_density", "value": 1.225}]
    result = AgentResult.from_dict(payload)

    first = result.to_dict()
    first["assumptions"][0]["value"] = 999

    second = result.to_dict()
    assert second["assumptions"][0]["value"] == 1.225


def test_complex_structured_payload_round_trips() -> None:
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "USER_REQUIREMENT",
                "statement": "endurance >= 30 min",
            },
            {
                "classification": "CALCULATED_VALUE",
                "statement": "estimated hover power",
                "value_w": 420.5,
            },
        ],
        "assumptions": [
            {"name": "air_density_kg_m3", "value": 1.225}
        ],
        "calculations": [
            {
                "name": "energy_budget",
                "inputs": [420.5, 0.5],
                "result_wh": 210.25,
            }
        ],
        "artifacts": [
            {"kind": "mass_budget", "state": "DRAFT"}
        ],
        "risks": [
            {"category": "ENERGY", "severity": "MEDIUM"}
        ],
        "missing_information": ["Battery usable capacity is unknown."],
        "open_questions": ["What reserve percentage should be required?"],
    }

    result = AgentResult.from_dict(payload)
    assert result.to_dict() == payload
    assert json.loads(json.dumps(result.to_dict())) == payload
