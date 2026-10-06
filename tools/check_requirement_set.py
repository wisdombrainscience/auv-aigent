#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.requirements import (  # noqa: E402
    Requirement,
    RequirementSet,
    RequirementSetStatus,
)


def main() -> int:
    value = RequirementSet(
        project_id="UAV-PROJ-000001",
        status=RequirementSetStatus.DRAFT,
        requirements=(
            Requirement(
                requirement_id="REQ-000001",
                text="Payload shall not exceed 2 kg.",
            ),
            Requirement(
                requirement_id="REQ-000002",
                text="Minimum endurance shall be 20 minutes.",
            ),
        ),
    )

    reopened = RequirementSet.from_dict(value.to_dict())
    if reopened != value:
        print("REQUIREMENT_SET=FAIL reason=round_trip_mismatch")
        return 2

    print(
        "REQUIREMENT_SET=PASS "
        f"requirements={len(value.requirements)} "
        f"status={value.status.value} "
        "provenance=USER_REQUIREMENT "
        "versioning=reserved_for_S3_5"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
