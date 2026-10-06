#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(description="Verify a Remote UAV CP0 manifest and its frozen artifacts.")
    p.add_argument("manifest", help="Path to cp0_manifest.json")
    args = p.parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent

    errors: list[str] = []
    if data.get("baseline_id") != "RUAV-AI-ARCH-V8.0-BL1":
        errors.append("unexpected_baseline_id")
    if data.get("cp0", {}).get("status") != "GREEN":
        errors.append("manifest_not_green")

    archive_name = data.get("source_snapshot", {}).get("archive")
    archive_sha = data.get("source_snapshot", {}).get("archive_sha256")
    if not archive_name or not archive_sha:
        errors.append("missing_source_archive_identity")
    else:
        archive = base / archive_name
        if not archive.is_file():
            errors.append("source_archive_missing")
        elif sha256_file(archive) != archive_sha:
            errors.append("source_archive_hash_mismatch")

    source_manifest = base / data.get("source_snapshot", {}).get("manifest", "source_manifest.json")
    if not source_manifest.is_file():
        errors.append("source_manifest_missing")

    for name in ("regression", "single_flight", "mission"):
        if data.get("checks", {}).get(name, {}).get("status") != "PASS":
            errors.append(f"{name}_not_pass")

    if errors:
        print("CP0_VERIFY=FAIL")
        for item in errors:
            print(f"ERROR={item}")
        return 2
    print("CP0_VERIFY=PASS")
    print(f"SOURCE_ARCHIVE_SHA256={archive_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
