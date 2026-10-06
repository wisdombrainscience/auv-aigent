from __future__ import annotations

import json

import pytest

from ai.contracts.provenance import (
    PROVENANCE_CONTRACT_VERSION,
    PROVENANCE_DESCRIPTIONS,
    ProvenanceClass,
    ProvenanceError,
    describe_provenance_class,
    parse_provenance_class,
    serialize_provenance_class,
)


EXPECTED = [
    "USER_FACT",
    "VERIFIED_FACT",
    "USER_REQUIREMENT",
    "ENGINEERING_ASSUMPTION",
    "AI_PROPOSAL",
    "CALCULATED_VALUE",
    "SIMULATION_RESULT",
    "VERIFIER_RESULT",
]


def test_contract_version_is_explicit() -> None:
    assert PROVENANCE_CONTRACT_VERSION == "1.0"


def test_frozen_provenance_set_and_order_are_exact() -> None:
    assert [item.value for item in ProvenanceClass] == EXPECTED


@pytest.mark.parametrize("raw", EXPECTED)
def test_parse_and_serialize_round_trip(raw: str) -> None:
    classification = parse_provenance_class(raw)
    payload = json.loads(
        json.dumps(
            {"classification": serialize_provenance_class(classification)}
        )
    )
    assert payload == {"classification": raw}
    assert parse_provenance_class(payload["classification"]) is classification


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        " USER_FACT",
        "USER_FACT ",
        "user_fact",
        "User_Fact",
        "USER FACT",
        "VERIFIED-FACT",
        "UNKNOWN",
        1,
        None,
    ],
)
def test_noncanonical_values_are_rejected(raw: object) -> None:
    with pytest.raises(ProvenanceError):
        parse_provenance_class(raw)  # type: ignore[arg-type]


def test_every_class_has_exactly_one_nonempty_description() -> None:
    assert set(PROVENANCE_DESCRIPTIONS) == set(ProvenanceClass)
    for classification in ProvenanceClass:
        description = describe_provenance_class(classification)
        assert isinstance(description, str)
        assert description.strip() == description
        assert description


def test_user_fact_description_does_not_claim_verification() -> None:
    text = describe_provenance_class(ProvenanceClass.USER_FACT).lower()
    assert "not independently verified" in text


def test_ai_proposal_description_is_advisory() -> None:
    text = describe_provenance_class(ProvenanceClass.AI_PROPOSAL).lower()
    assert "advisory" in text
    assert "approval" in text


def test_verifier_result_is_distinct_from_simulation_result() -> None:
    assert ProvenanceClass.VERIFIER_RESULT is not ProvenanceClass.SIMULATION_RESULT
    assert (
        serialize_provenance_class(ProvenanceClass.VERIFIER_RESULT)
        == "VERIFIER_RESULT"
    )
    assert (
        serialize_provenance_class(ProvenanceClass.SIMULATION_RESULT)
        == "SIMULATION_RESULT"
    )


def test_requirement_is_distinct_from_user_fact() -> None:
    assert ProvenanceClass.USER_REQUIREMENT is not ProvenanceClass.USER_FACT


def test_assumption_is_distinct_from_fact_and_requirement() -> None:
    assumption = ProvenanceClass.ENGINEERING_ASSUMPTION
    assert assumption is not ProvenanceClass.USER_FACT
    assert assumption is not ProvenanceClass.VERIFIED_FACT
    assert assumption is not ProvenanceClass.USER_REQUIREMENT


def test_calculated_value_does_not_alias_verified_fact() -> None:
    assert ProvenanceClass.CALCULATED_VALUE is not ProvenanceClass.VERIFIED_FACT


def test_description_mapping_is_immutable() -> None:
    with pytest.raises(TypeError):
        PROVENANCE_DESCRIPTIONS[ProvenanceClass.USER_FACT] = "changed"  # type: ignore[index]
