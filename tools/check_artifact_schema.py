#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.artifacts import (  # noqa: E402
    ArtifactInputVersion,
    ArtifactRecord,
)
from ai.contracts.artifact_states import ArtifactState  # noqa: E402


def main() -> int:
    record = ArtifactRecord(
        artifact_id="ART-000001",
        project_id="UAV-PROJ-000001",
        version=1,
        type="mass_budget",
        producer_task="TASK-000001",
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
        hash="a" * 64,
        status=ArtifactState.DRAFT,
        validation_result=None,
        created_timestamp="2026-10-03T18:30:00Z",
    )

    reopened = ArtifactRecord.from_dict(record.to_dict())
    if reopened != record:
        print("ARTIFACT_SCHEMA=FAIL reason=round_trip")
        return 2

    print(
        "ARTIFACT_SCHEMA=PASS "
        "fields=10 "
        "dependencies=2 "
        "status=DRAFT "
        "hash=sha256 "
        "versioning=reserved_for_A4_2 "
        "edges=reserved_for_A4_3"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
