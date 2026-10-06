from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import ai.project.assumptions as assumptions_module
from ai.contracts.provenance import ProvenanceClass
from ai.project.assumptions import (
    ASSUMPTION_CONTRACT_VERSION,
    ASSUMPTION_FILENAME,
    ASSUMPTION_FORMAT_VERSION,
    ASSUMPTION_HASH_FILENAME,
    ASSUMPTION_STORE_CONTRACT_VERSION,
    MAX_ASSUMPTIONS_PER_SET,
    MAX_ASSUMPTION_JSON_DEPTH,
    MAX_ASSUMPTION_KEY_CHARS,
    MAX_ASSUMPTION_STORE_BYTES,
    Assumption,
    AssumptionContractError,
    AssumptionSet,
    AssumptionSetNotFoundError,
    AssumptionStore,
    AssumptionStoreConflictError,
)
from ai.project.facts import Fact
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def make_store(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return AssumptionStore(projects), projects, handle


def assumption(
    key="airframe.drag_coefficient",
    value=0.8,
    provenance="ENGINEERING_ASSUMPTION",
):
    return Assumption(
        key=key,
        value=value,
        provenance=provenance,
    )


def test_versions_and_limits_are_explicit() -> None:
    assert ASSUMPTION_CONTRACT_VERSION == "1.0"
    assert ASSUMPTION_STORE_CONTRACT_VERSION == "1.0"
    assert ASSUMPTION_FORMAT_VERSION == 1
    assert ASSUMPTION_FILENAME == "assumptions.json"
    assert ASSUMPTION_HASH_FILENAME == "assumptions.json.sha256"
    assert MAX_ASSUMPTIONS_PER_SET == 256
    assert MAX_ASSUMPTION_KEY_CHARS == 128
    assert MAX_ASSUMPTION_JSON_DEPTH == 16
    assert MAX_ASSUMPTION_STORE_BYTES == 2 * 1024 * 1024


def test_assumption_accepts_only_engineering_assumption_provenance() -> None:
    item = assumption()
    assert (
        item.provenance
        is ProvenanceClass.ENGINEERING_ASSUMPTION
    )


@pytest.mark.parametrize(
    "prov",
    [
        "USER_FACT",
        "VERIFIED_FACT",
        "USER_REQUIREMENT",
        "AI_PROPOSAL",
        "CALCULATED_VALUE",
        "SIMULATION_RESULT",
        "VERIFIER_RESULT",
    ],
)
def test_assumption_rejects_non_assumption_provenance(
    prov: str,
) -> None:
    with pytest.raises(
        AssumptionContractError,
        match="ENGINEERING_ASSUMPTION",
    ):
        assumption(provenance=prov)


def test_assumption_never_becomes_fact_implicitly() -> None:
    item = assumption()
    assert item.provenance is ProvenanceClass.ENGINEERING_ASSUMPTION
    assert item.provenance not in {
        ProvenanceClass.USER_FACT,
        ProvenanceClass.VERIFIED_FACT,
    }


@pytest.mark.parametrize(
    "key",
    [
        "",
        " airframe.drag",
        "airframe drag",
        ".drag",
        "a" * 129,
    ],
)
def test_assumption_key_is_strict(key: str) -> None:
    with pytest.raises(AssumptionContractError):
        assumption(key=key)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        2.5,
        "X500",
        [1, 2, {"x": 3}],
        {"unit": "kg", "value": 2.5},
    ],
)
def test_assumption_value_accepts_strict_json(value) -> None:
    item = assumption(value=value)
    assert Assumption.from_dict(item.to_dict()) == item


@pytest.mark.parametrize(
    "value",
    [
        math.nan,
        math.inf,
        -math.inf,
        {1: "bad"},
        object(),
    ],
)
def test_assumption_value_rejects_non_strict_json(value) -> None:
    with pytest.raises(AssumptionContractError):
        assumption(value=value)


def test_assumptionset_rejects_duplicate_keys() -> None:
    with pytest.raises(
        AssumptionContractError,
        match="duplicate assumption key",
    ):
        AssumptionSet(
            project_id="UAV-PROJ-000001",
            assumptions=(
                assumption("a"),
                assumption("a", value=2),
            ),
        )


def test_assumptionset_is_bounded() -> None:
    values = tuple(
        assumption(f"a{index}", index)
        for index in range(MAX_ASSUMPTIONS_PER_SET)
    )
    assert (
        len(
            AssumptionSet(
                project_id="UAV-PROJ-000001",
                assumptions=values,
            ).assumptions
        )
        == 256
    )
    with pytest.raises(
        AssumptionContractError,
        match="MAX_ASSUMPTIONS_PER_SET",
    ):
        AssumptionSet(
            project_id="UAV-PROJ-000001",
            assumptions=values + (
                assumption("overflow", 1),
            ),
        )


def test_round_trip_preserves_order_and_provenance() -> None:
    value = AssumptionSet(
        project_id="UAV-PROJ-000001",
        assumptions=(
            assumption("airframe.drag_coefficient", 0.8),
            assumption("battery.usable_fraction", 0.8),
        ),
    )
    assert AssumptionSet.from_dict(value.to_dict()) == value
    assert [
        item.key
        for item in value.assumptions
    ] == [
        "airframe.drag_coefficient",
        "battery.usable_fraction",
    ]


def test_store_persists_and_restarts(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    value = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(
            assumption(
                "airframe.drag_coefficient",
                0.8,
            ),
        ),
    )
    store.save(value)
    assert store.open(handle.project_id) == value

    restarted = AssumptionStore(
        ProjectStore(projects.projects_root)
    )
    assert restarted.open(handle.project_id) == value


def test_store_writes_hash_sidecar(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    value = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(assumption(),),
    )
    store.save(value)

    assert (
        handle.layout.project_dir / ASSUMPTION_FILENAME
    ).is_file()
    assert (
        handle.layout.project_dir / ASSUMPTION_HASH_FILENAME
    ).is_file()


def test_store_detects_tamper(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    value = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(assumption(),),
    )
    store.save(value)

    path = (
        handle.layout.project_dir
        / ASSUMPTION_FILENAME
    )
    path.write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        AssumptionStoreConflictError,
        match="hash mismatch",
    ):
        store.open(handle.project_id)


def test_missing_assumption_set_is_explicit(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    with pytest.raises(AssumptionSetNotFoundError):
        store.open(handle.project_id)


def test_assumption_file_symlink_is_rejected(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")
    (
        handle.layout.project_dir
        / ASSUMPTION_FILENAME
    ).symlink_to(external)

    with pytest.raises(
        AssumptionStoreConflictError,
        match="symbolic link",
    ):
        store.open(handle.project_id)


def test_duplicate_json_key_is_rejected_even_with_matching_hash(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    path = (
        handle.layout.project_dir
        / ASSUMPTION_FILENAME
    )
    hash_path = (
        handle.layout.project_dir
        / ASSUMPTION_HASH_FILENAME
    )
    raw = (
        '{"format_version":1,'
        '"project_id":"UAV-PROJ-000001",'
        '"assumptions":[],"assumptions":[]}\n'
    ).encode()

    path.write_bytes(raw)
    hash_path.write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(
        AssumptionStoreConflictError,
        match="duplicate JSON key",
    ):
        store.open(handle.project_id)


def test_save_replaces_current_set_without_reclassification(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(assumption("vehicle.cd", 0.8),),
    )
    second = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(assumption("vehicle.cd", 0.9),),
    )

    store.save(first)
    store.save(second)
    reopened = store.open(handle.project_id)

    assert reopened == second
    assert (
        reopened.assumptions[0].provenance
        is ProvenanceClass.ENGINEERING_ASSUMPTION
    )


def test_hash_write_failure_restores_previous_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(assumption("a", 1),),
    )
    second = AssumptionSet(
        project_id=handle.project_id,
        assumptions=(assumption("a", 2),),
    )
    store.save(first)
    original = assumptions_module._atomic_replace

    def fail_hash(path, raw):
        if path.name == ASSUMPTION_HASH_FILENAME:
            raise OSError("injected hash failure")
        return original(path, raw)

    monkeypatch.setattr(
        assumptions_module,
        "_atomic_replace",
        fail_hash,
    )
    with pytest.raises(OSError, match="injected"):
        store.save(second)

    monkeypatch.setattr(
        assumptions_module,
        "_atomic_replace",
        original,
    )
    assert store.open(handle.project_id) == first


def test_s3_7_does_not_change_sqlite_schema(
    tmp_path: Path,
) -> None:
    store, projects, handle = make_store(tmp_path)
    store.save(
        AssumptionSet(
            project_id=handle.project_id,
            assumptions=(assumption(),),
        )
    )

    import sqlite3

    with sqlite3.connect(
        projects.database_path
    ) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        )

    assert tables == PROJECT_SCHEMA_TABLES


def test_fact_type_does_not_accept_assumption_provenance() -> None:
    with pytest.raises(ValueError):
        Fact(
            key="airframe.drag_coefficient",
            value=0.8,
            provenance="ENGINEERING_ASSUMPTION",
        )


def test_s3_7_exposes_no_fact_promotion_or_decision_api(
    tmp_path: Path,
) -> None:
    store, _projects, _handle = make_store(tmp_path)
    for name in (
        "promote_to_fact",
        "verify",
        "facts",
        "decisions",
        "record_decision",
        "transaction",
    ):
        assert not hasattr(store, name)


def test_assumption_module_has_no_ai_network_or_uav_runtime_imports() -> None:
    text = Path(
        assumptions_module.__file__
    ).read_text(
        encoding="utf-8"
    ).lower()

    for forbidden in (
        "ollama",
        "requests",
        "httpx",
        "mavsdk",
        "px4",
        "gazebo",
    ):
        assert forbidden not in text


def test_assumption_module_has_no_retry_or_sleep_loop() -> None:
    text = Path(
        assumptions_module.__file__
    ).read_text(
        encoding="utf-8"
    )
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
