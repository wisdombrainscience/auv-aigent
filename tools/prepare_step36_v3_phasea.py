#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, shutil
from pathlib import Path

DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def write_workspace(root: Path, model: dict, run_name: str):
    run = root / "models" / model["model_id"] / run_name
    ws = run / "workspace"
    if run.exists():
        raise SystemExit(f"REFUSE: run already exists: {run}")
    (ws / ".opencode/agents").mkdir(parents=True, exist_ok=False)

    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "model": "ollama/bench-model",
        "providers": {
            "ollama": {
                "settings": {"baseURL": "http://127.0.0.1:11434/v1"},
                "models": {
                    "bench-model": {
                        "modelID": model["benchmark_tag"],
                        "name": f"NAV-001 STEP36 V3 {model['model_id']}"
                    }
                }
            }
        }
    }
    (ws / "opencode.jsonc").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(root / "frozen/generator.md", ws / ".opencode/agents/generator.md")

    meta = {
        "model_id": model["model_id"],
        "source_tag": model["source_tag"],
        "benchmark_tag": model["benchmark_tag"],
        "run_name": run_name,
        "opencode_model": "ollama/bench-model",
        "num_ctx": 8192,
        "generator_sha256": sha256(ws / ".opencode/agents/generator.md"),
        "prompt_sha256": sha256(root / "frozen/implementation_prompt.txt"),
        "benchmark_contract_sha256": sha256(root / "frozen/benchmark_contract.txt"),
    }
    (run / "run_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    root = args.root.expanduser().resolve()
    manifest_path = root / "benchmark_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("FAIL: benchmark_manifest.json missing")
    manifest = load_json(manifest_path)

    # Verify frozen hashes before creating official workspaces.
    expected = manifest["hashes"]
    checks = {
        "implementation_prompt_sha256": root / "frozen/implementation_prompt.txt",
        "generator_sha256": root / "frozen/generator.md",
        "benchmark_contract_sha256": root / "frozen/benchmark_contract.txt",
    }
    for key, path in checks.items():
        got = sha256(path)
        if got != expected[key]:
            raise SystemExit(f"FAIL: frozen hash mismatch: {key}")

    for model in manifest["models"]:
        write_workspace(root, model, "smoke")
        write_workspace(root, model, "run_001_scored")

    print("=== STEP36 V3 PHASE A WORKSPACE PREP ===")
    print("FROZEN_HASHES=PASS")
    print(f"MODELS={len(manifest['models'])}")
    for m in manifest["models"]:
        print(f"{m['model_id']}=READY tag={m['benchmark_tag']}")
    print("STEP36_V3_PHASEA_WORKSPACES=PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
