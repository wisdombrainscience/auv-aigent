#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"

GENERATOR_PROFILE = """---
description: Generates one NAV-001 implementation artifact from frozen context
mode: primary
model: ollama/bench-model
steps: 1
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
  - action: webfetch
    resource: "*"
    effect: deny
  - action: websearch
    resource: "*"
    effect: deny
  - action: subagent
    resource: "*"
    effect: deny
---

You are the NAV-001 benchmark generation role.

Use only the frozen context supplied in the user prompt.
Do not inspect or modify repository files.
Do not run commands.
Do not access any accepted implementation.

Return only the exact FILE protocol requested by the prompt.
"""

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()

    model_cfg = project / "benchmark/step36_models.json"
    prompt = root / "frozen/implementation_prompt.txt"
    if not model_cfg.is_file():
        raise SystemExit(f"FAIL: missing {model_cfg}")
    if not prompt.is_file():
        raise SystemExit(f"FAIL: missing frozen prompt {prompt}")

    cfg = load_json(model_cfg)

    print("=== NAV-001 STEP 36A V2 REPAIR ===")
    print("PRESERVE_RUN_001=YES")
    print("FIX_AGENT_PROFILE=generator")
    print("FIX_MODEL_ROUTING=PER_MODEL_LOCAL_CONFIG")
    print("COMMON_OPENCODE_ALIAS=ollama/bench-model")

    for model in cfg["models"]:
        model_id = model["id"]
        tag = model["ollama_tag"]
        run = root / "models" / model_id / "run_002_semantic"
        ws = run / "workspace"
        if run.exists():
            raise SystemExit(f"FAIL: immutable semantic run already exists: {run}")
        (ws / ".opencode/agents").mkdir(parents=True, exist_ok=True)

        opencode_cfg = {
            "$schema": "https://opencode.ai/config.json",
            "model": "ollama/bench-model",
            "providers": {
                "ollama": {
                    "settings": {"baseURL": "http://127.0.0.1:11434/v1"},
                    "models": {
                        "bench-model": {
                            "modelID": tag,
                            "name": f"NAV-001 STEP36 benchmark {tag}"
                        }
                    }
                }
            }
        }
        (ws / "opencode.jsonc").write_text(json.dumps(opencode_cfg, indent=2) + "\n", encoding="utf-8")
        (ws / ".opencode/agents/generator.md").write_text(GENERATOR_PROFILE, encoding="utf-8")

        meta = {
            "model_id": model_id,
            "ollama_tag": tag,
            "opencode_model": "ollama/bench-model",
            "semantic_attempt": True,
            "run_name": "run_002_semantic",
            "run_001_preserved": True,
            "repair_reason": [
                "run_001 called missing agent profile 'generator'",
                "run_001 passed raw Ollama tag as OpenCode model alias",
                "all run_001 workspaces inherited the same qwen3-agent-8k provider mapping"
            ]
        }
        (run / "model.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"{model_id}=READY tag={tag}")

    print("STEP36A_V2_REPAIR=PASS")
    print(f"ROOT={root}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
