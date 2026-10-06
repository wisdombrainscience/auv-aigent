from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import ai.project.facts as facts_module
from ai.contracts.provenance import ProvenanceClass
from ai.project.facts import (
    FACT_CONTRACT_VERSION,
    FACT_FILENAME,
    FACT_FORMAT_VERSION,
    FACT_HASH_FILENAME,
    FACT_STORE_CONTRACT_VERSION,
    MAX_FACTS_PER_SET,
    MAX_FACT_JSON_DEPTH,
    MAX_FACT_KEY_CHARS,
    MAX_FACT_STORE_BYTES,
    Fact,
    FactContractError,
    FactSet,
    FactSetNotFoundError,
    FactStore,
    FactStoreConflictError,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def make_store(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    return FactStore(projects), projects, handle


def fact(key="vehicle.mass_kg", value=2.5, provenance="USER_FACT"):
    return Fact(key=key, value=value, provenance=provenance)


def test_versions_and_limits_are_explicit():
    assert FACT_CONTRACT_VERSION == "1.0"
    assert FACT_STORE_CONTRACT_VERSION == "1.0"
    assert FACT_FORMAT_VERSION == 1
    assert FACT_FILENAME == "facts.json"
    assert FACT_HASH_FILENAME == "facts.json.sha256"
    assert MAX_FACTS_PER_SET == 256
    assert MAX_FACT_KEY_CHARS == 128
    assert MAX_FACT_JSON_DEPTH == 16
    assert MAX_FACT_STORE_BYTES == 2 * 1024 * 1024


@pytest.mark.parametrize("prov", ["USER_FACT", "VERIFIED_FACT"])
def test_fact_accepts_only_fact_provenance(prov):
    item = fact(provenance=prov)
    assert item.provenance.value == prov


@pytest.mark.parametrize(
    "prov",
    [
        "USER_REQUIREMENT",
        "ENGINEERING_ASSUMPTION",
        "AI_PROPOSAL",
        "CALCULATED_VALUE",
        "SIMULATION_RESULT",
        "VERIFIER_RESULT",
    ],
)
def test_fact_rejects_non_fact_provenance(prov):
    with pytest.raises(FactContractError, match="USER_FACT or VERIFIED_FACT"):
        fact(provenance=prov)


def test_user_fact_is_never_promoted():
    item = fact(provenance="USER_FACT")
    assert item.provenance is ProvenanceClass.USER_FACT
    assert item.provenance is not ProvenanceClass.VERIFIED_FACT


@pytest.mark.parametrize(
    "key",
    ["", " vehicle.mass_kg", "vehicle mass", ".mass", "a" * 129],
)
def test_fact_key_is_strict(key):
    with pytest.raises(FactContractError):
        fact(key=key)


@pytest.mark.parametrize(
    "value",
    [None, True, 7, 2.5, "PX4", [1, 2, {"x": 3}], {"unit": "kg", "value": 2.5}],
)
def test_fact_value_accepts_strict_json(value):
    item = fact(value=value)
    assert Fact.from_dict(item.to_dict()) == item


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, {1: "bad"}, object()])
def test_fact_value_rejects_non_strict_json(value):
    with pytest.raises(FactContractError):
        fact(value=value)


def test_factset_rejects_duplicate_keys():
    with pytest.raises(FactContractError, match="duplicate fact key"):
        FactSet(
            project_id="UAV-PROJ-000001",
            facts=(fact("a"), fact("a", value=2)),
        )


def test_factset_is_bounded():
    values = tuple(fact(f"f{index}", index) for index in range(MAX_FACTS_PER_SET))
    assert len(FactSet(project_id="UAV-PROJ-000001", facts=values).facts) == 256
    with pytest.raises(FactContractError, match="MAX_FACTS_PER_SET"):
        FactSet(
            project_id="UAV-PROJ-000001",
            facts=values + (fact("overflow", 1),),
        )


def test_factset_round_trip_preserves_order_and_provenance():
    value = FactSet(
        project_id="UAV-PROJ-000001",
        facts=(
            fact("vehicle.mass_kg", 2.5, "USER_FACT"),
            fact("physics.gravity_m_s2", 9.80665, "VERIFIED_FACT"),
        ),
    )
    assert FactSet.from_dict(value.to_dict()) == value
    assert [x.key for x in value.facts] == ["vehicle.mass_kg", "physics.gravity_m_s2"]


def test_store_persists_and_restarts(tmp_path):
    store, projects, handle = make_store(tmp_path)
    value = FactSet(
        project_id=handle.project_id,
        facts=(fact("vehicle.mass_kg", 2.5),),
    )
    store.save(value)
    assert store.open(handle.project_id) == value
    restarted = FactStore(ProjectStore(projects.projects_root))
    assert restarted.open(handle.project_id) == value


def test_store_writes_hash_sidecar(tmp_path):
    store, _projects, handle = make_store(tmp_path)
    value = FactSet(project_id=handle.project_id, facts=(fact(),))
    store.save(value)
    assert (handle.layout.project_dir / FACT_FILENAME).is_file()
    assert (handle.layout.project_dir / FACT_HASH_FILENAME).is_file()


def test_store_detects_tamper(tmp_path):
    store, _projects, handle = make_store(tmp_path)
    value = FactSet(project_id=handle.project_id, facts=(fact(),))
    store.save(value)
    path = handle.layout.project_dir / FACT_FILENAME
    path.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(FactStoreConflictError, match="hash mismatch"):
        store.open(handle.project_id)


def test_missing_factset_is_explicit(tmp_path):
    store, _projects, handle = make_store(tmp_path)
    with pytest.raises(FactSetNotFoundError):
        store.open(handle.project_id)


def test_fact_file_symlink_is_rejected(tmp_path):
    store, _projects, handle = make_store(tmp_path)
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")
    (handle.layout.project_dir / FACT_FILENAME).symlink_to(external)
    with pytest.raises(FactStoreConflictError, match="symbolic link"):
        store.open(handle.project_id)


def test_duplicate_json_key_is_rejected_even_with_matching_hash(tmp_path):
    store, _projects, handle = make_store(tmp_path)
    path = handle.layout.project_dir / FACT_FILENAME
    hash_path = handle.layout.project_dir / FACT_HASH_FILENAME
    raw = (
        '{"format_version":1,"project_id":"UAV-PROJ-000001",'
        '"facts":[],"facts":[]}\n'
    ).encode()
    path.write_bytes(raw)
    import hashlib
    hash_path.write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
    with pytest.raises(FactStoreConflictError, match="duplicate JSON key"):
        store.open(handle.project_id)


def test_save_replaces_current_factset_without_provenance_conversion(tmp_path):
    store, _projects, handle = make_store(tmp_path)
    first = FactSet(
        project_id=handle.project_id,
        facts=(fact("vehicle.mass_kg", 2.5, "USER_FACT"),),
    )
    second = FactSet(
        project_id=handle.project_id,
        facts=(fact("vehicle.mass_kg", 2.6, "USER_FACT"),),
    )
    store.save(first)
    store.save(second)
    reopened = store.open(handle.project_id)
    assert reopened == second
    assert reopened.facts[0].provenance is ProvenanceClass.USER_FACT


def test_hash_write_failure_restores_previous_factset(tmp_path, monkeypatch):
    store, _projects, handle = make_store(tmp_path)
    first = FactSet(project_id=handle.project_id, facts=(fact("a", 1),))
    second = FactSet(project_id=handle.project_id, facts=(fact("a", 2),))
    store.save(first)
    original = facts_module._atomic_replace

    def fail_hash(path, raw):
        if path.name == FACT_HASH_FILENAME:
            raise OSError("injected hash failure")
        return original(path, raw)

    monkeypatch.setattr(facts_module, "_atomic_replace", fail_hash)
    with pytest.raises(OSError, match="injected"):
        store.save(second)
    monkeypatch.setattr(facts_module, "_atomic_replace", original)
    assert store.open(handle.project_id) == first


def test_s3_6_does_not_change_sqlite_schema(tmp_path):
    store, projects, handle = make_store(tmp_path)
    store.save(FactSet(project_id=handle.project_id, facts=(fact(),)))
    import sqlite3
    with sqlite3.connect(projects.database_path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert tables == PROJECT_SCHEMA_TABLES


def test_s3_6_exposes_no_assumption_decision_or_promotion_api(tmp_path):
    store, _projects, _handle = make_store(tmp_path)
    for name in (
        "promote",
        "verify",
        "assumptions",
        "decisions",
        "add_assumption",
        "record_decision",
        "transaction",
    ):
        assert not hasattr(store, name)


def test_fact_module_has_no_ai_network_or_uav_runtime_imports():
    text = Path(facts_module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("ollama", "requests", "httpx", "mavsdk", "px4", "gazebo"):
        assert forbidden not in text


def test_fact_module_has_no_retry_or_sleep_loop():
    text = Path(facts_module.__file__).read_text(encoding="utf-8")
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
