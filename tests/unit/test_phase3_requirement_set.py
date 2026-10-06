from __future__ import annotations

import pytest

from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.project.requirements import (
    MAX_REQUIREMENTS_PER_SET,
    MAX_REQUIREMENT_TEXT_BYTES,
    REQUIREMENT_SET_CONTRACT_VERSION,
    Requirement,
    RequirementContractError,
    RequirementSet,
    RequirementSetStatus,
    parse_requirement_set,
)


def req(sequence: int, text: str = "Payload shall not exceed 2 kg.") -> Requirement:
    return Requirement(
        requirement_id=CanonicalId.from_sequence(
            IdKind.REQUIREMENT,
            sequence,
        ),
        text=text,
    )


def requirement_set() -> RequirementSet:
    return RequirementSet(
        project_id=CanonicalId.from_sequence(
            IdKind.PROJECT,
            1,
        ),
        status=RequirementSetStatus.DRAFT,
        requirements=(
            req(1),
            req(2, "Minimum endurance shall be 20 minutes."),
        ),
    )


def test_contract_version_is_explicit() -> None:
    assert REQUIREMENT_SET_CONTRACT_VERSION == "1.0"


def test_contract_limits_are_bounded() -> None:
    assert MAX_REQUIREMENTS_PER_SET == 256
    assert MAX_REQUIREMENT_TEXT_BYTES == 16 * 1024


def test_requirement_accepts_exact_user_requirement_provenance() -> None:
    item = req(1)
    assert item.provenance is ProvenanceClass.USER_REQUIREMENT


@pytest.mark.parametrize(
    "bad",
    [
        ProvenanceClass.USER_FACT,
        ProvenanceClass.VERIFIED_FACT,
        ProvenanceClass.ENGINEERING_ASSUMPTION,
        ProvenanceClass.AI_PROPOSAL,
        ProvenanceClass.CALCULATED_VALUE,
        ProvenanceClass.SIMULATION_RESULT,
        ProvenanceClass.VERIFIER_RESULT,
    ],
)
def test_requirement_rejects_non_user_requirement_provenance(
    bad: ProvenanceClass,
) -> None:
    with pytest.raises(
        RequirementContractError,
        match="exactly USER_REQUIREMENT",
    ):
        Requirement(
            requirement_id=CanonicalId.from_sequence(
                IdKind.REQUIREMENT,
                1,
            ),
            text="Requirement text.",
            provenance=bad,
        )


def test_requirement_requires_requirement_id() -> None:
    with pytest.raises(
        RequirementContractError,
        match="requirement ID",
    ):
        Requirement(
            requirement_id=CanonicalId.from_sequence(
                IdKind.TASK,
                1,
            ),
            text="Requirement text.",
        )


@pytest.mark.parametrize(
    "text",
    [
        "",
        " leading",
        "trailing ",
        "\x00",
    ],
)
def test_requirement_text_rejects_invalid_values(text: str) -> None:
    with pytest.raises(RequirementContractError):
        Requirement(
            requirement_id=CanonicalId.from_sequence(
                IdKind.REQUIREMENT,
                1,
            ),
            text=text,
        )


def test_requirement_text_preserves_internal_newlines() -> None:
    text = "Maximum mass shall be 2 kg.\nApplies at takeoff."
    item = req(1, text)
    assert item.text == text


def test_requirement_text_limit_is_enforced_by_utf8_bytes() -> None:
    okay = "a" * MAX_REQUIREMENT_TEXT_BYTES
    assert req(1, okay).text == okay

    with pytest.raises(
        RequirementContractError,
        match="MAX_REQUIREMENT_TEXT_BYTES",
    ):
        req(1, "a" * (MAX_REQUIREMENT_TEXT_BYTES + 1))


def test_requirement_strict_json_round_trip() -> None:
    item = req(1)
    payload = item.to_dict()
    assert payload == {
        "requirement_id": "REQ-000001",
        "text": "Payload shall not exceed 2 kg.",
        "provenance": "USER_REQUIREMENT",
    }
    assert Requirement.from_dict(payload) == item


@pytest.mark.parametrize(
    "payload",
    [
        {
            "requirement_id": "REQ-000001",
            "text": "x",
        },
        {
            "requirement_id": "REQ-000001",
            "text": "x",
            "provenance": "USER_REQUIREMENT",
            "extra": 1,
        },
    ],
)
def test_requirement_json_rejects_missing_or_extra_fields(payload) -> None:
    with pytest.raises(RequirementContractError):
        Requirement.from_dict(payload)


def test_requirement_set_accepts_draft_and_approved_representation() -> None:
    draft = requirement_set()
    approved = RequirementSet(
        project_id=draft.project_id,
        status=RequirementSetStatus.APPROVED,
        requirements=draft.requirements,
    )
    assert draft.status is RequirementSetStatus.DRAFT
    assert approved.status is RequirementSetStatus.APPROVED


def test_requirement_set_requires_project_id() -> None:
    with pytest.raises(
        RequirementContractError,
        match="project ID",
    ):
        RequirementSet(
            project_id=CanonicalId.from_sequence(
                IdKind.TASK,
                1,
            ),
            status=RequirementSetStatus.DRAFT,
            requirements=(req(1),),
        )


@pytest.mark.parametrize(
    "bad",
    [
        "draft",
        "APPROVED ",
        "REVIEWED",
        "",
    ],
)
def test_requirement_set_rejects_unknown_status(bad: str) -> None:
    with pytest.raises(RequirementContractError):
        RequirementSet(
            project_id=CanonicalId.from_sequence(
                IdKind.PROJECT,
                1,
            ),
            status=bad,  # type: ignore[arg-type]
            requirements=(req(1),),
        )


def test_requirement_set_preserves_requirement_order() -> None:
    value = requirement_set()
    assert [
        item.requirement_id.value
        for item in value.requirements
    ] == ["REQ-000001", "REQ-000002"]


def test_requirement_set_rejects_duplicate_ids() -> None:
    with pytest.raises(
        RequirementContractError,
        match="duplicate requirement ID",
    ):
        RequirementSet(
            project_id=CanonicalId.from_sequence(
                IdKind.PROJECT,
                1,
            ),
            status=RequirementSetStatus.DRAFT,
            requirements=(
                req(1, "A"),
                req(1, "B"),
            ),
        )


def test_requirement_set_allows_empty_draft_without_inventing_requirements() -> None:
    value = RequirementSet(
        project_id=CanonicalId.from_sequence(
            IdKind.PROJECT,
            1,
        ),
        status=RequirementSetStatus.DRAFT,
        requirements=(),
    )
    assert value.requirements == ()


def test_requirement_set_limit_is_enforced() -> None:
    items = tuple(
        req(index + 1, f"Requirement {index + 1}.")
        for index in range(MAX_REQUIREMENTS_PER_SET)
    )
    value = RequirementSet(
        project_id=CanonicalId.from_sequence(
            IdKind.PROJECT,
            1,
        ),
        status=RequirementSetStatus.DRAFT,
        requirements=items,
    )
    assert len(value.requirements) == MAX_REQUIREMENTS_PER_SET

    with pytest.raises(
        RequirementContractError,
        match="MAX_REQUIREMENTS_PER_SET",
    ):
        RequirementSet(
            project_id=value.project_id,
            status=value.status,
            requirements=items + (req(MAX_REQUIREMENTS_PER_SET + 1),),
        )


def test_requirement_set_round_trip_is_deterministic() -> None:
    value = requirement_set()
    payload = value.to_dict()
    assert payload == {
        "project_id": "UAV-PROJ-000001",
        "status": "DRAFT",
        "requirements": [
            {
                "requirement_id": "REQ-000001",
                "text": "Payload shall not exceed 2 kg.",
                "provenance": "USER_REQUIREMENT",
            },
            {
                "requirement_id": "REQ-000002",
                "text": "Minimum endurance shall be 20 minutes.",
                "provenance": "USER_REQUIREMENT",
            },
        ],
    }
    assert parse_requirement_set(payload) == value


@pytest.mark.parametrize(
    "payload",
    [
        {
            "project_id": "UAV-PROJ-000001",
            "status": "DRAFT",
        },
        {
            "project_id": "UAV-PROJ-000001",
            "status": "DRAFT",
            "requirements": [],
            "version": 1,
        },
    ],
)
def test_requirement_set_rejects_missing_extra_fields(payload) -> None:
    with pytest.raises(RequirementContractError):
        RequirementSet.from_dict(payload)


def test_s3_4_does_not_introduce_requirement_version_field() -> None:
    payload = requirement_set().to_dict()
    assert "version" not in payload
    assert "previous_version" not in payload
    assert "change_request" not in payload


def test_s3_4_does_not_allocate_requirement_ids() -> None:
    value = RequirementSet(
        project_id="UAV-PROJ-000001",
        status="DRAFT",
        requirements=(
            Requirement(
                requirement_id="REQ-000042",
                text="Operator altitude shall use positive-up convention.",
            ),
        ),
    )
    assert value.requirements[0].requirement_id.value == "REQ-000042"


def test_s3_4_does_not_silently_reclassify_text() -> None:
    text = "This value is uncertain but the user explicitly requires it."
    item = Requirement(
        requirement_id="REQ-000001",
        text=text,
        provenance="USER_REQUIREMENT",
    )
    assert item.text == text
    assert item.provenance.value == "USER_REQUIREMENT"
