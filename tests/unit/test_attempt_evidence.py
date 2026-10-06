import json
from pathlib import Path

import pytest

from src.evidence.attempt import (
    AttemptEvidence,
    EvidenceFinalizedError,
)


def test_attempt_structure(tmp_path: Path):
    evidence = AttemptEvidence(
        root=tmp_path,
        task_id="NAV-001",
        scenario_id="T01",
        attempt=1,
    )

    path = evidence.create()

    assert path == (
        tmp_path
        / "NAV-001"
        / "T01"
        / "attempt_001"
    )

    assert (
        path / "events.jsonl"
    ).exists()


def test_write_metadata_and_events(tmp_path: Path):
    evidence = AttemptEvidence(
        tmp_path,
        "NAV-001",
        "T01",
        1,
    )

    evidence.create()

    evidence.write_metadata(
        {
            "simulator": "PX4 SITL",
            "frame": "LOCAL_NED",
        }
    )

    evidence.append_event(
        "experiment_started",
        {"source": "unit-test"},
    )

    metadata = json.loads(
        (
            evidence.path
            / "metadata.json"
        ).read_text()
    )

    assert metadata["task_id"] == "NAV-001"
    assert metadata["attempt"] == 1

    events = (
        evidence.path
        / "events.jsonl"
    ).read_text()

    assert "experiment_started" in events


def test_finalize_blocks_api_writes(tmp_path: Path):
    evidence = AttemptEvidence(
        tmp_path,
        "NAV-001",
        "T01",
        1,
    )

    evidence.create()

    evidence.write_json(
        "input.json",
        {"target": [1, 2, -3]},
    )

    evidence.finalize()

    with pytest.raises(
        EvidenceFinalizedError
    ):
        evidence.write_json(
            "module_output.json",
            {"status": "fake"},
        )


def test_integrity_passes_after_finalize(
    tmp_path: Path,
):
    evidence = AttemptEvidence(
        tmp_path,
        "NAV-001",
        "T01",
        1,
    )

    evidence.create()

    evidence.write_json(
        "input.json",
        {"hello": "world"},
    )

    evidence.finalize()

    ok, problems = (
        evidence.verify_integrity()
    )

    assert ok is True
    assert problems == []


def test_integrity_detects_tampering(
    tmp_path: Path,
):
    evidence = AttemptEvidence(
        tmp_path,
        "NAV-001",
        "T01",
        1,
    )

    evidence.create()

    p = evidence.write_json(
        "input.json",
        {"hello": "world"},
    )

    evidence.finalize()

    # Simulate an external actor bypassing
    # the normal Evidence API.
    p.chmod(0o644)
    p.write_text(
        '{"hello":"tampered"}\n'
    )

    ok, problems = (
        evidence.verify_integrity()
    )

    assert ok is False

    assert any(
        item.startswith(
            "hash_mismatch:input.json"
        )
        for item in problems
    )
