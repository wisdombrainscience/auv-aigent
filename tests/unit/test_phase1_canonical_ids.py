from __future__ import annotations

import json

import pytest

from ai.contracts.ids import (
    ID_CONTRACT_VERSION,
    CanonicalId,
    CanonicalIdError,
    DuplicateCanonicalIdError,
    IdKind,
    ensure_unique_ids,
)


@pytest.mark.parametrize(
    ("kind", "sequence", "expected"),
    [
        (IdKind.PROJECT, 1, "UAV-PROJ-000001"),
        (IdKind.REQUIREMENT, 1, "REQ-000001"),
        (IdKind.TASK, 42, "TASK-000042"),
        (IdKind.ARTIFACT, 7, "ART-000007"),
        (IdKind.AGENT_RUN, 5, "AI-RUN-000005"),
        (IdKind.REVIEW, 9, "REVIEW-000009"),
        (IdKind.VALIDATION, 12, "VALID-000012"),
        (IdKind.ISSUE, 41, "ISSUE-0041"),
    ],
)
def test_from_sequence_uses_canonical_format(
    kind: IdKind, sequence: int, expected: str
) -> None:
    value = CanonicalId.from_sequence(kind, sequence)
    assert value.kind is kind
    assert value.value == expected
    assert value.sequence == sequence
    assert str(value) == expected


def test_contract_version_is_explicit() -> None:
    assert ID_CONTRACT_VERSION == "1.0"


def test_frozen_baseline_examples_parse() -> None:
    assert CanonicalId.parse("UAV-PROJ-000001") == CanonicalId.from_sequence(
        IdKind.PROJECT, 1
    )
    assert CanonicalId.parse("ISSUE-0041") == CanonicalId.from_sequence(
        IdKind.ISSUE, 41
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        " TASK-000001",
        "TASK-000001 ",
        "TASK 000001",
        "task-000001",
        "TASK-1",
        "TASK-00001",
        "TASK-000000",
        "TASK-1000000",
        "UNKNOWN-000001",
        "NAV-001",
        "MISSION-001",
    ],
)
def test_parse_rejects_noncanonical_or_runtime_ids(raw: str) -> None:
    with pytest.raises(CanonicalIdError):
        CanonicalId.parse(raw)


@pytest.mark.parametrize("sequence", [0, -1, 1.0, "1", True, None])
def test_from_sequence_rejects_invalid_sequences(sequence: object) -> None:
    with pytest.raises(CanonicalIdError):
        CanonicalId.from_sequence(IdKind.TASK, sequence)  # type: ignore[arg-type]


def test_from_sequence_rejects_kind_specific_overflow() -> None:
    with pytest.raises(CanonicalIdError):
        CanonicalId.from_sequence(IdKind.ISSUE, 10_000)
    with pytest.raises(CanonicalIdError):
        CanonicalId.from_sequence(IdKind.TASK, 1_000_000)


def test_constructor_rejects_kind_value_mismatch() -> None:
    with pytest.raises(CanonicalIdError):
        CanonicalId(IdKind.TASK, "ART-000001")


def test_json_round_trip_is_lossless() -> None:
    original = CanonicalId.from_sequence(IdKind.ARTIFACT, 123)
    wire = json.loads(json.dumps(original.to_dict(), sort_keys=True))
    restored = CanonicalId.from_dict(wire)
    assert restored == original
    assert hash(restored) == hash(original)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"kind": "task"},
        {"value": "TASK-000001"},
        {"kind": "task", "value": "TASK-000001", "extra": 1},
        {"kind": "bogus", "value": "TASK-000001"},
        {"kind": "task", "value": "ART-000001"},
    ],
)
def test_from_dict_rejects_malformed_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(CanonicalIdError):
        CanonicalId.from_dict(payload)


def test_duplicate_collision_is_rejected() -> None:
    with pytest.raises(DuplicateCanonicalIdError, match="TASK-000001"):
        ensure_unique_ids(["TASK-000001", CanonicalId.parse("TASK-000001")])


def test_same_sequence_in_different_namespaces_is_not_a_collision() -> None:
    ids = ensure_unique_ids(["TASK-000001", "ART-000001", "REQ-000001"])
    assert [item.kind for item in ids] == [
        IdKind.TASK,
        IdKind.ARTIFACT,
        IdKind.REQUIREMENT,
    ]


def test_canonical_id_is_immutable() -> None:
    value = CanonicalId.from_sequence(IdKind.TASK, 1)
    with pytest.raises(Exception):
        value.value = "TASK-000002"  # type: ignore[misc]
