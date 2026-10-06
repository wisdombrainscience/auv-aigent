#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.schema import initialize_project_schema  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-s3-1-") as tmp:
        path = Path(tmp) / "project.sqlite3"
        with sqlite3.connect(path) as connection:
            first = initialize_project_schema(connection)
        with sqlite3.connect(path) as connection:
            reopened = initialize_project_schema(connection)

    if first != reopened:
        print("PROJECT_SCHEMA=FAIL reason=restart_mismatch")
        return 2

    print(
        "PROJECT_SCHEMA=PASS "
        f"version={reopened.version} "
        f"tables={len(reopened.tables)} "
        f"id_kinds={len(reopened.id_kinds)} "
        "restart=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
