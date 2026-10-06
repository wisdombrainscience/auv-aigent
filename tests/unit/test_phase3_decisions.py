from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import ai.project.decisions as decisions_module
from ai.project.assumptions import Assumption
from ai.project.decisions import (
    DECISION_CONTRACT_VERSION,
    DECISION_FILENAME,
    DECISION_FORMAT_VERSION,
    DECISION_HASH_FILENAME,
    DECISION_STORE_CONTRACT_VERSION,
    MAX_DECISIONS_PER_SET,
    MAX_DECISION_JSON_DEPTH,
    MAX_DECISION_KEY_CHARS,
    MAX_DECISION_RATIONALE_BYTES,
    MAX_DECISION_STORE_BYTES,
    Decision,
    DecisionContractError,
    DecisionSet,
    DecisionSetNotFoundError,
    DecisionStore,
    DecisionStoreConflictError,
)
from ai.project.facts import Fact
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def make_store(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return DecisionStore(projects), projects, handle


def decision(
    key="airframe.frame_type",
    value="quad_x",
    rationale="Selected for the current simulation architecture.",
):
    return Decision(
        key=key,
        value=value,
        rationale=rationale,
    )


def test_versions_and_limits_are_explicit() -> None:
    assert DECISION_CONTRACT_VERSION == "1.0"
    assert DECISION_STORE_CONTRACT_VERSION == "1.0"
    assert DECISION_FORMAT_VERSION == 1
    assert DECISION_FILENAME == "decisions.json"
    assert DECISION_HASH_FILENAME == "decisions.json.sha256"
    assert MAX_DECISIONS_PER_SET == 256
    assert MAX_DECISION_KEY_CHARS == 128
    assert MAX_DECISION_RATIONALE_BYTES == 16 * 1024
    assert MAX_DECISION_JSON_DEPTH == 16
    assert MAX_DECISION_STORE_BYTES == 2 * 1024 * 1024


def test_decision_exact_fields_are_key_value_rationale() -> None:
    item = decision()
    assert set(item.to_dict()) == {
        "key",
        "value",
        "rationale",
    }


def test_decision_does_not_invent_provenance_or_approval_fields() -> None:
    payload = decision().to_dict()
    for forbidden in (
        "provenance",
        "approved",
        "approved_by",
        "status",
        "authority",
    ):
        assert forbidden not in payload


@pytest.mark.parametrize(
    "key",
    [
        "",
        " airframe.frame_type",
        "airframe frame",
        ".airframe",
        "a" * 129,
    ],
)
def test_decision_key_is_strict(key: str) -> None:
    with pytest.raises(DecisionContractError):
        decision(key=key)


@pytest.mark.parametrize(
    "rationale",
    [
        "",
        " leading",
        "trailing ",
        "\x00",
    ],
)
def test_rationale_is_strict(rationale: str) -> None:
    with pytest.raises(DecisionContractError):
        decision(rationale=rationale)


def test_rationale_limit_is_utf8_bytes() -> None:
    okay = "a" * MAX_DECISION_RATIONALE_BYTES
    assert decision(rationale=okay).rationale == okay

    with pytest.raises(
        DecisionContractError,
        match="MAX_DECISION_RATIONALE_BYTES",
    ):
        decision(
            rationale="a" * (MAX_DECISION_RATIONALE_BYTES + 1)
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        2.5,
        "quad_x",
        [1, 2, {"x": 3}],
        {"frame": "quad_x", "motors": 4},
    ],
)
def test_decision_value_accepts_strict_json(value) -> None:
    item = decision(value=value)
    assert Decision.from_dict(item.to_dict()) == item


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
def test_decision_value_rejects_non_strict_json(value) -> None:
    with pytest.raises(DecisionContractError):
        decision(value=value)


def test_decisionset_rejects_duplicate_keys() -> None:
    with pytest.raises(
        DecisionContractError,
        match="duplicate decision key",
    ):
        DecisionSet(
            project_id="UAV-PROJ-000001",
            decisions=(
                decision("airframe.type", "x"),
                decision("airframe.type", "plus"),
            ),
        )


def test_decisionset_is_bounded() -> None:
    values = tuple(
        decision(
            key=f"d{index}",
            value=index,
            rationale=f"Reason {index}.",
        )
        for index in range(MAX_DECISIONS_PER_SET)
    )
    value = DecisionSet(
        project_id="UAV-PROJ-000001",
        decisions=values,
    )
    assert len(value.decisions) == MAX_DECISIONS_PER_SET

    with pytest.raises(
        DecisionContractError,
        match="MAX_DECISIONS_PER_SET",
    ):
        DecisionSet(
            project_id="UAV-PROJ-000001",
            decisions=values
            + (
                decision(
                    key="overflow",
                    value=True,
                    rationale="Overflow.",
                ),
            ),
        )


def test_decisionset_round_trip_preserves_order() -> None:
    value = DecisionSet(
        project_id="UAV-PROJ-000001",
        decisions=(
            decision(
                "airframe.frame_type",
                "quad_x",
                "Matches the PX4 simulation configuration.",
            ),
            decision(
                "navigation.frame",
                "LOCAL_NED",
                "Preserves the existing internal coordinate convention.",
            ),
        ),
    )
    assert DecisionSet.from_dict(value.to_dict()) == value
    assert [
        item.key for item in value.decisions
    ] == [
        "airframe.frame_type",
        "navigation.frame",
    ]


def test_fact_and_assumption_objects_are_not_decisions() -> None:
    fact = Fact(
        key="vehicle.mass_kg",
        value=2.5,
        provenance="USER_FACT",
    )
    assumption = Assumption(
        key="airframe.drag_coefficient",
        value=0.8,
    )
    assert not isinstance(fact, Decision)
    assert not isinstance(assumption, Decision)


def test_store_persists_and_restarts(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    value = DecisionSet(
        project_id=handle.project_id,
        decisions=(decision(),),
    )
    store.save(value)
    assert store.open(handle.project_id) == value

    restarted = DecisionStore(
        ProjectStore(projects.projects_root)
    )
    assert restarted.open(handle.project_id) == value


def test_store_writes_hash_sidecar(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    value = DecisionSet(
        project_id=handle.project_id,
        decisions=(decision(),),
    )
    store.save(value)

    assert (
        handle.layout.project_dir / DECISION_FILENAME
    ).is_file()
    assert (
        handle.layout.project_dir / DECISION_HASH_FILENAME
    ).is_file()


def test_store_detects_tamper(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    value = DecisionSet(
        project_id=handle.project_id,
        decisions=(decision(),),
    )
    store.save(value)

    path = handle.layout.project_dir / DECISION_FILENAME
    path.write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        DecisionStoreConflictError,
        match="hash mismatch",
    ):
        store.open(handle.project_id)


def test_missing_decision_set_is_explicit(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    with pytest.raises(DecisionSetNotFoundError):
        store.open(handle.project_id)


def test_decision_file_symlink_is_rejected(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")

    (
        handle.layout.project_dir / DECISION_FILENAME
    ).symlink_to(external)

    with pytest.raises(
        DecisionStoreConflictError,
        match="symbolic link",
    ):
        store.open(handle.project_id)


def test_duplicate_json_key_is_rejected_even_with_matching_hash(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    path = handle.layout.project_dir / DECISION_FILENAME
    hash_path = (
        handle.layout.project_dir / DECISION_HASH_FILENAME
    )
    raw = (
        '{"format_version":1,'
        '"project_id":"UAV-PROJ-000001",'
        '"decisions":[],"decisions":[]}\n'
    ).encode()

    path.write_bytes(raw)
    hash_path.write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(
        DecisionStoreConflictError,
        match="duplicate JSON key",
    ):
        store.open(handle.project_id)


def test_save_replaces_current_decision_set(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)

    first = DecisionSet(
        project_id=handle.project_id,
        decisions=(
            decision(
                "airframe.frame_type",
                "quad_x",
                "Initial simulation choice.",
            ),
        ),
    )
    second = DecisionSet(
        project_id=handle.project_id,
        decisions=(
            decision(
                "airframe.frame_type",
                "quad_plus",
                "Updated project choice.",
            ),
        ),
    )

    store.save(first)
    store.save(second)
    assert store.open(handle.project_id) == second


def test_hash_write_failure_restores_previous_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _projects, handle = make_store(tmp_path)

    first = DecisionSet(
        project_id=handle.project_id,
        decisions=(
            decision("a", 1, "First."),
        ),
    )
    second = DecisionSet(
        project_id=handle.project_id,
        decisions=(
            decision("a", 2, "Second."),
        ),
    )
    store.save(first)

    original = decisions_module._atomic_replace

    def fail_hash(path, raw):
        if path.name == DECISION_HASH_FILENAME:
            raise OSError("injected hash failure")
        return original(path, raw)

    monkeypatch.setattr(
        decisions_module,
        "_atomic_replace",
        fail_hash,
    )

    with pytest.raises(OSError, match="injected"):
        store.save(second)

    monkeypatch.setattr(
        decisions_module,
        "_atomic_replace",
        original,
    )
    assert store.open(handle.project_id) == first


def test_s3_8_does_not_change_sqlite_schema(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    store.save(
        DecisionSet(
            project_id=handle.project_id,
            decisions=(decision(),),
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


def test_s3_8_exposes_no_approval_lineage_or_transaction_api(
    tmp_path: Path,
) -> None:
    store, _projects, _handle = make_store(tmp_path)

    for name in (
        "approve",
        "reject",
        "review",
        "authority",
        "mark_stale",
        "invalidate_artifacts",
        "transaction",
        "begin",
        "commit",
        "rollback",
    ):
        assert not hasattr(store, name)


def test_decision_module_has_no_ai_network_or_uav_runtime_imports() -> None:
    text = Path(
        decisions_module.__file__
    ).read_text(encoding="utf-8").lower()

    for forbidden in (
        "ollama",
        "requests",
        "httpx",
        "mavsdk",
        "px4",
        "gazebo",
    ):
        assert forbidden not in text


def test_decision_module_has_no_retry_or_sleep_loop() -> None:
    text = Path(
        decisions_module.__file__
    ).read_text(encoding="utf-8")

    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
