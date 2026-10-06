#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_OLD = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"
DEFAULT_V3 = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"

MODELS = [
    ("Q3_4B_AGENT_8K", "qwen3:4b-agent-8k", "nav001-bench:q3-agent-8k"),
    ("Q3_4B_AGENT_32K", "qwen3:4b-agent-32k", "nav001-bench:q3-agent32k-ctx8k"),
    ("Q3_4B_INSTRUCT", "qwen3:4b-instruct", "nav001-bench:q3-instruct-ctx8k"),
    ("Q25_CODER_3B_32K", "qwen2.5-coder:3b-32k", "nav001-bench:q25-coder32k-ctx8k"),
    ("Q25_CODER_3B_INSTRUCT", "qwen2.5-coder:3b-instruct", "nav001-bench:q25-instruct-ctx8k"),
]

GENERATOR = """---
description: Generates one NAV-001 implementation artifact from frozen context
mode: primary
model: ollama/bench-model
steps: 4
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

CONTRACT = """NAV-001 STEP36 BENCHMARK V3

same_frozen_prompt=true
same_generator_profile=true
same_num_ctx=8192
same_timeout_s=900
same_output_protocol=true
same_candidate_validator=true
same_boundary_policy=true
same_targeted_tests=true
same_full_tests=true

first_attempts_per_model=1
manual_candidate_edit=false
manual_protocol_repair=false
accepted_candidate_source_exposed=false

smoke_test_scored=false
operator_abort_scored=false
infrastructure_failure_scored=false

semantic_protocol_failure_scored=true
candidate_validation_failure_scored=true

generation_and_sitl_concurrent=false
scope=SIMULATION_ONLY
"""

def run(cmd, timeout=600):
    p = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return p.returncode, p.stdout

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def assert_base_model(tag: str):
    rc, out = run(["ollama", "show", tag], timeout=60)
    if rc != 0:
        raise SystemExit(f"FAIL: base model unavailable: {tag}\n{out}")

def create_tag(src: str, dst: str):
    with tempfile.NamedTemporaryFile("w", suffix=".modelfile", delete=False) as f:
        f.write(f"FROM {src}\nPARAMETER num_ctx 8192\n")
        modelfile = Path(f.name)
    try:
        rc, out = run(["ollama", "create", dst, "-f", str(modelfile)], timeout=900)
        if rc != 0:
            raise SystemExit(f"FAIL: ollama create {dst}\n{out}")
        return out
    finally:
        modelfile.unlink(missing_ok=True)

def verify_ctx(tag: str) -> str:
    rc, out = run(["ollama", "show", tag], timeout=60)
    if rc != 0:
        raise SystemExit(f"FAIL: cannot inspect {tag}\n{out}")
    if not re.search(r"\bnum_ctx\s+8192\b", out):
        raise SystemExit(
            f"FAIL: {tag} does not report num_ctx 8192\n"
            "Do not continue the official benchmark.\n\n" + out
        )
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--old-root", type=Path, default=DEFAULT_OLD)
    ap.add_argument("--v3-root", type=Path, default=DEFAULT_V3)
    args = ap.parse_args()

    old = args.old_root.expanduser().resolve()
    v3 = args.v3_root.expanduser().resolve()
    old_prompt = old / "frozen/implementation_prompt.txt"

    if not old_prompt.is_file():
        raise SystemExit(f"FAIL: frozen prompt missing: {old_prompt}")
    if v3.exists():
        raise SystemExit(
            f"REFUSE: V3 root already exists: {v3}\n"
            "Inspect it instead of overwriting benchmark evidence."
        )

    print("=== STEP36 V3 MODEL NORMALIZATION ===")
    manifest_models = []
    for model_id, src, dst in MODELS:
        assert_base_model(src)
        print(f"CREATE {model_id}: {src} -> {dst}")
        create_tag(src, dst)
        verify_ctx(dst)
        print(f"{model_id}_CTX=PASS num_ctx=8192")
        manifest_models.append({
            "model_id": model_id,
            "source_tag": src,
            "benchmark_tag": dst,
            "num_ctx": 8192,
        })

    frozen = v3 / "frozen"
    frozen.mkdir(parents=True, exist_ok=False)
    (v3 / "models").mkdir()
    (v3 / "results").mkdir()

    prompt_dst = frozen / "implementation_prompt.txt"
    prompt_dst.write_text(old_prompt.read_text(encoding="utf-8"), encoding="utf-8")
    (frozen / "generator.md").write_text(GENERATOR, encoding="utf-8")
    (frozen / "benchmark_contract.txt").write_text(CONTRACT, encoding="utf-8")

    hashes = {
        "implementation_prompt_sha256": sha256(prompt_dst),
        "generator_sha256": sha256(frozen / "generator.md"),
        "benchmark_contract_sha256": sha256(frozen / "benchmark_contract.txt"),
    }

    manifest = {
        "task": "NAV-001",
        "step": "36",
        "benchmark_version": "V3",
        "scope": "PX4_SITL_SIMULATION_ONLY",
        "normalized_num_ctx": 8192,
        "timeout_s": 900,
        "models": manifest_models,
        "hashes": hashes,
        "old_step36_used_as_official_score": False,
        "old_step36_role": "pilot_harness_debug_evidence",
    }
    (v3 / "benchmark_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print()
    print("=== STEP36 V3 FREEZE ===")
    print(f"MODELS_NORMALIZED={len(manifest_models)}/5")
    print("ALL_NUM_CTX_8192=PASS")
    print(f"PROMPT_SHA256={hashes['implementation_prompt_sha256']}")
    print(f"GENERATOR_SHA256={hashes['generator_sha256']}")
    print(f"CONTRACT_SHA256={hashes['benchmark_contract_sha256']}")
    print(f"V3_ROOT={v3}")
    print("STEP36_V3_PREPARE=PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
