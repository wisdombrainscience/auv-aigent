from __future__ import annotations

import pytest

from ai.artifacts import (
    ARTIFACT_SCHEMA_CONTRACT_VERSION,
    MAX_ARTIFACT_INPUT_DEPENDENCIES,
    MAX_ARTIFACT_TYPE_CHARS,
    MAX_ARTIFACT_VERSION,
    ArtifactInputVersion,
    ArtifactRecord,
    ArtifactSchemaError,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


SHA = "a" * 64


def validation_pass() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck(
                check_id="finite-values",
                status=ValidationStatus.PASS,
                field=None,
                reason=None,
                details={},
            ),
        )
    )


def artifact(**overrides) -> ArtifactRecord:
    data = {
        "artifact_id": "ART-000001",
        "project_id": "UAV-PROJ-000001",
        "version": 1,
        "type": "mass_budget",
        "producer_task": "TASK-000001",
        "input_dependency_versions": (
            ArtifactInputVersion(
                entity_id="REQ-000001",
                version=2,
            ),
        ),
        "hash": SHA,
        "status": ArtifactState.DRAFT,
        "validation_result": None,
        "created_timestamp": "2026-10-03T18:30:00Z",
    }
    data.update(overrides)
    return ArtifactRecord(**data)


def test_contract_and_limits_are_explicit() -> None:
    assert ARTIFACT_SCHEMA_CONTRACT_VERSION == "1.0"
    assert MAX_ARTIFACT_VERSION == 999_999
    assert MAX_ARTIFACT_TYPE_CHARS == 128
    assert MAX_ARTIFACT_INPUT_DEPENDENCIES == 64


def test_schema_contains_frozen_baseline_fields_plus_project_scope() -> None:
    assert set(artifact().to_dict()) == {
        "artifact_id",
        "project_id",
        "version",
        "type",
        "producer_task",
        "input_dependency_versions",
        "hash",
        "status",
        "validation_result",
        "created_timestamp",
    }


def test_artifact_requires_artifact_id() -> None:
    with pytest.raises(
        ArtifactSchemaError,
        match="artifact_id",
    ):
        artifact(artifact_id="TASK-000001")


def test_artifact_requires_project_id() -> None:
    with pytest.raises(
        ArtifactSchemaError,
        match="project_id",
    ):
        artifact(project_id="ART-000001")


def test_artifact_requires_producer_task_id() -> None:
    with pytest.raises(
        ArtifactSchemaError,
        match="producer_task",
    ):
        artifact(producer_task="AI-RUN-000001")


@pytest.mark.parametrize("version", [0, -1, 1_000_000, True, 1.5])
def test_artifact_version_is_bounded_positive_integer(version) -> None:
    with pytest.raises(ArtifactSchemaError):
        artifact(version=version)


@pytest.mark.parametrize(
    "artifact_type",
    [
        "",
        " mass_budget",
        "mass budget",
        ".mass_budget",
        "a" * 129,
    ],
)
def test_artifact_type_is_strict(artifact_type: str) -> None:
    with pytest.raises(ArtifactSchemaError):
        artifact(type=artifact_type)


def test_dependency_reference_accepts_requirement_and_artifact_versions() -> None:
    record = artifact(
        input_dependency_versions=(
            ArtifactInputVersion(
                entity_id="REQ-000001",
                version=3,
            ),
            ArtifactInputVersion(
                entity_id="ART-000002",
                version=4,
            ),
        )
    )
    assert [
        item.entity_id.kind
        for item in record.input_dependency_versions
    ] == [
        IdKind.REQUIREMENT,
        IdKind.ARTIFACT,
    ]


@pytest.mark.parametrize(
    "bad_id",
    [
        "TASK-000001",
        "AI-RUN-000001",
        "VALID-000001",
        "UAV-PROJ-000001",
    ],
)
def test_dependency_reference_rejects_non_requirement_artifact_ids(
    bad_id: str,
) -> None:
    with pytest.raises(ArtifactSchemaError):
        ArtifactInputVersion(
            entity_id=bad_id,
            version=1,
        )


def test_duplicate_dependency_version_is_rejected() -> None:
    dependency = ArtifactInputVersion(
        entity_id="REQ-000001",
        version=1,
    )
    with pytest.raises(
        ArtifactSchemaError,
        match="duplicate input dependency",
    ):
        artifact(
            input_dependency_versions=(
                dependency,
                dependency,
            )
        )


def test_dependency_set_is_bounded() -> None:
    dependencies = tuple(
        ArtifactInputVersion(
            entity_id=CanonicalId.from_sequence(
                IdKind.ARTIFACT,
                index + 2,
            ),
            version=1,
        )
        for index in range(MAX_ARTIFACT_INPUT_DEPENDENCIES)
    )
    assert (
        len(
            artifact(
                input_dependency_versions=dependencies
            ).input_dependency_versions
        )
        == 64
    )

    with pytest.raises(
        ArtifactSchemaError,
        match="MAX_ARTIFACT_INPUT_DEPENDENCIES",
    ):
        artifact(
            input_dependency_versions=dependencies
            + (
                ArtifactInputVersion(
                    entity_id="ART-000100",
                    version=1,
                ),
            )
        )


@pytest.mark.parametrize(
    "digest",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "",
    ],
)
def test_hash_field_is_strict_lowercase_sha256(digest: str) -> None:
    with pytest.raises(ArtifactSchemaError):
        artifact(hash=digest)


@pytest.mark.parametrize(
    "state",
    list(ArtifactState),
)
def test_schema_accepts_every_frozen_artifact_state(
    state: ArtifactState,
) -> None:
    assert artifact(status=state).status is state


def test_validation_result_may_be_null_before_validation() -> None:
    assert artifact().validation_result is None


def test_validation_result_can_embed_c1_7_contract() -> None:
    result = validation_pass()
    assert artifact(
        status=ArtifactState.VALIDATED,
        validation_result=result,
    ).validation_result == result


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-03 18:30:00Z",
        "2026-10-03T18:30:00+00:00",
        "2026-10-03T18:30:00",
        "2026-13-03T18:30:00Z",
        "",
    ],
)
def test_created_timestamp_is_canonical_utc(timestamp: str) -> None:
    with pytest.raises(ArtifactSchemaError):
        artifact(created_timestamp=timestamp)


def test_fractional_utc_timestamp_is_accepted() -> None:
    record = artifact(
        created_timestamp="2026-10-03T18:30:00.123456Z"
    )
    assert record.created_timestamp.endswith("Z")


def test_strict_round_trip_is_deterministic() -> None:
    original = artifact(
        input_dependency_versions=(
            ArtifactInputVersion(
                entity_id="REQ-000001",
                version=2,
            ),
            ArtifactInputVersion(
                entity_id="ART-000002",
                version=1,
            ),
        ),
        status=ArtifactState.VALIDATED,
        validation_result=validation_pass(),
    )

    payload = original.to_dict()
    assert ArtifactRecord.from_dict(payload) == original


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.pop("hash"),
        lambda payload: payload.update({"extra": 1}),
    ],
)
def test_strict_payload_rejects_missing_or_extra_fields(mutator) -> None:
    payload = artifact().to_dict()
    mutator(payload)
    with pytest.raises(ArtifactSchemaError):
        ArtifactRecord.from_dict(payload)


def test_a4_1_does_not_compute_hash() -> None:
    record = artifact(hash="b" * 64)
    assert record.hash == "b" * 64


def test_a4_1_does_not_allocate_artifact_ids_or_versions() -> None:
    record = artifact(
        artifact_id="ART-000042",
        version=7,
    )
    assert record.artifact_id.value == "ART-000042"
    assert record.version == 7


def test_a4_1_does_not_apply_state_transition_logic() -> None:
    # A4.1 is a record parser: historical state is representable because old
    # artifact versions must remain readable. A4.5 owns transition execution.
    assert artifact(
        status=ArtifactState.SUPERSEDED
    ).status is ArtifactState.SUPERSEDED


def test_a4_1_does_not_define_dependency_graph_semantics() -> None:
    record = artifact(
        input_dependency_versions=(
            ArtifactInputVersion(
                entity_id="ART-000001",
                version=1,
            ),
        )
    )
    # A self-reference is schema-representable here. A4.3/A4.8 must reject it
    # when building/validating the persisted dependency graph.
    assert record.input_dependency_versions[0].entity_id == record.artifact_id
