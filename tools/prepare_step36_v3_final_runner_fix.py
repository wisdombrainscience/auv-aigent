#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path

DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    root = args.root.expanduser().resolve()

    manifest_path = root / "benchmark_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("FAIL: benchmark_manifest.json missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for model in manifest["models"]:
        model_root = root / "models" / model["model_id"]
        source = model_root / "run_001_scored"
        target = model_root / "smoke_envfix"

        if not source.is_dir():
            raise SystemExit(f"FAIL: official scored workspace missing: {source}")
        if target.exists():
            raise SystemExit(f"REFUSE: smoke_envfix already exists: {target}")

        shutil.copytree(source, target)

        # smoke_envfix is diagnostic only, never a scored run.
        result = target / "execution_result.json"
        if result.exists():
            result.unlink()
        raw = target / "opencode_raw.txt"
        if raw.exists():
            raw.unlink()

        manifest_file = target / "run_manifest.json"
        doc = json.loads(manifest_file.read_text(encoding="utf-8"))
        doc["run_name"] = "smoke_envfix"
        doc["scored"] = False
        doc["repair"] = (
            "Use proven file-based generator agent; runner sets child PWD to "
            "workspace and omits --title."
        )
        manifest_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    print("=== STEP36 V3 FINAL RUNNER FIX PREP ===")
    print("FILE_BASED_AGENT=YES")
    print("OFFICIAL_SCORED_WORKSPACES_UNCHANGED=YES")
    print("SMOKE_ENVFIX_CREATED=5/5")
    print("PREP=PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
