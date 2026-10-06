#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--failures", type=Path, required=True)
    ap.add_argument("--test-log", type=Path)
    ap.add_argument("--stage", default="UNIT_TEST")
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()
    failures_path = (
        args.failures if args.failures.is_absolute() else project / args.failures
    ).resolve()
    output = (
        args.output if args.output.is_absolute() else project / args.output
    ).resolve()
    contract = project / "specs/NAV-001-IMPLEMENTATION-CONTRACT.yaml"

    if not candidate.is_file():
        raise SystemExit(f"candidate missing: {candidate}")
    if not failures_path.is_file():
        raise SystemExit(f"failures missing: {failures_path}")
    if not contract.is_file():
        raise SystemExit(f"contract missing: {contract}")

    failures_doc = json.loads(failures_path.read_text(encoding="utf-8"))
    failures = failures_doc.get("failures", failures_doc)
    if not isinstance(failures, list) or not failures:
        raise SystemExit("failures must be a non-empty list")

    package = {
        "task": "NAV-001",
        "attempt_id": args.attempt_id,
        "stage": args.stage,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate": {
            "path": str(candidate),
            "sha256": sha256(candidate),
        },
        "contract": {
            "path": str(contract),
            "sha256": sha256(contract),
        },
        "failures": failures,
        "recovery_policy": {
            "mode": "MINIMAL_UNIFIED_DIFF_ONLY",
            "allowed_paths": [
                "src/capabilities/navigation/go_to_target.py"
            ],
            "must_not_modify_tests": True,
            "must_not_modify_spec": True,
            "must_not_modify_verifier": True,
        },
    }

    if args.test_log:
        log_path = (
            args.test_log if args.test_log.is_absolute() else project / args.test_log
        ).resolve()
        package["test_log"] = {
            "path": str(log_path),
            "sha256": sha256(log_path) if log_path.is_file() else None,
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"FAILURE_PACKAGE: PASS")
    print(f"OUTPUT={output}")
    print(f"FAILURE_COUNT={len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
