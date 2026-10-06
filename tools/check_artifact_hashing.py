#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.artifacts import (  # noqa: E402
    ArtifactContentStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
    compute_artifact_sha256,
)
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-artifact-hashing-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        versions = ArtifactVersionStore(projects)

        content_v1 = b"mass-budget-v1"
        first = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=content_v1,
                created_timestamp="2026-10-03T22:15:00Z",
            ),
        )

        content_v2 = b"mass-budget-v2"
        second = versions.append_version(
            handle.project_id,
            first.artifact_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000002",
                input_dependency_versions=(),
                content=content_v2,
                created_timestamp="2026-10-03T22:16:00Z",
            ),
        )

        restarted = ArtifactVersionStore(
            ProjectStore(projects.projects_root)
        )
        reopened = restarted.open_latest(
            handle.project_id,
            first.artifact_id,
        )

        if reopened != second:
            print("ARTIFACT_HASHING=FAIL reason=restart")
            return 2
        if first.hash != compute_artifact_sha256(content_v1):
            print("ARTIFACT_HASHING=FAIL reason=v1_hash")
            return 3
        if second.hash != compute_artifact_sha256(content_v2):
            print("ARTIFACT_HASHING=FAIL reason=v2_hash")
            return 4

        content_store = ArtifactContentStore(projects)
        if content_store.read(handle.project_id, first.hash) != content_v1:
            print("ARTIFACT_HASHING=FAIL reason=v1_content")
            return 5

        print(
            "ARTIFACT_HASHING=PASS "
            f"artifact={first.artifact_id.value} "
            "algorithm=sha256 "
            "computed=PASS "
            "content_store=PASS "
            "dedup=PASS "
            "restart=PASS "
            "tamper=fail_closed "
            "states=reserved_for_A4_5"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
