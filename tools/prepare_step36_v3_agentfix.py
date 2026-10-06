#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"

SYSTEM_TEXT = """You are the NAV-001 benchmark generation role.

Use only the frozen context supplied in the user prompt.
Do not inspect or modify repository files.
Do not run commands.
Do not access any accepted implementation.

Return only the exact FILE protocol requested by the prompt.
"""

PERMISSIONS = [
    {"action": "edit", "resource": "*", "effect": "deny"},
    {"action": "shell", "resource": "*", "effect": "deny"},
    {"action": "webfetch", "resource": "*", "effect": "deny"},
    {"action": "websearch", "resource": "*", "effect": "deny"},
    {"action": "subagent", "resource": "*", "effect": "deny"},
]

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    root = args.root.expanduser().resolve()

    manifest_path = root / "benchmark_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("FAIL: benchmark_manifest.json missing")
    manifest = load_json(manifest_path)

    # Verify frozen files still match the official V3 manifest.
    checks = {
        "implementation_prompt_sha256": root / "frozen/implementation_prompt.txt",
        "generator_sha256": root / "frozen/generator.md",
        "benchmark_contract_sha256": root / "frozen/benchmark_contract.txt",
    }
    for key, path in checks.items():
        if sha256(path) != manifest["hashes"][key]:
            raise SystemExit(f"FAIL: frozen hash mismatch: {key}")

    print("=== STEP36 V3 AGENT DISCOVERY REPAIR ===")
    print("OLD_SMOKE_EVIDENCE=PRESERVED")
    print("OLD_RUN_001_SCORED_UNTOUCHED=YES")
    print("AGENT_DEFINITION=INLINE_OPENCODE_JSONC")
    print("DEFAULT_AGENT=generator")

    for model in manifest["models"]:
        for run_name in ("smoke_v2", "run_001_scored_v2"):
            run = root / "models" / model["model_id"] / run_name
            ws = run / "workspace"
            if run.exists():
                raise SystemExit(f"REFUSE: repair run already exists: {run}")
            ws.mkdir(parents=True, exist_ok=False)

            cfg = {
                "$schema": "https://opencode.ai/config.json",
                "model": "ollama/bench-model",
                "default_agent": "generator",
                "providers": {
                    "ollama": {
                        "settings": {"baseURL": "http://127.0.0.1:11434/v1"},
                        "models": {
                            "bench-model": {
                                "modelID": model["benchmark_tag"],
                                "name": f"NAV-001 STEP36 V3 {model['model_id']}",
                            }
                        },
                    }
                },
                "agents": {
                    "generator": {
                        "description": "Generates one NAV-001 implementation artifact from frozen context",
                        "mode": "primary",
                        "model": "ollama/bench-model",
                        "steps": 4,
                        "permissions": PERMISSIONS,
                        "system": SYSTEM_TEXT,
                    }
                },
            }
            config_path = ws / "opencode.jsonc"
            config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

            meta = {
                "model_id": model["model_id"],
                "benchmark_tag": model["benchmark_tag"],
                "run_name": run_name,
                "agent_transport": "inline_jsonc",
                "opencode_model": "ollama/bench-model",
                "num_ctx": 8192,
                "prompt_sha256": manifest["hashes"]["implementation_prompt_sha256"],
                "benchmark_contract_sha256": manifest["hashes"]["benchmark_contract_sha256"],
                "workspace_config_sha256": sha256(config_path),
                "previous_smoke_evidence_preserved": True,
            }
            (run / "run_manifest.json").write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )

        print(f"{model['model_id']}=READY")

    print("STEP36_V3_AGENT_REPAIR=PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
