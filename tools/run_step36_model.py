#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"
FILE_START = "<<<FILE:src/capabilities/navigation/go_to_target.py>>>"
FILE_END = "<<<END_FILE>>>"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def gpu_snapshot():
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return p.stdout.strip() if p.returncode == 0 else "UNAVAILABLE: " + p.stdout.strip()


def extract_candidate(raw: str) -> tuple[bool, str, str]:
    starts = [m.start() for m in re.finditer(re.escape(FILE_START), raw)]
    ends = [m.start() for m in re.finditer(re.escape(FILE_END), raw)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        return False, "", f"marker count start={len(starts)} end={len(ends)}"
    body_start = starts[0] + len(FILE_START)
    body = raw[body_start:ends[0]].strip()
    if not body:
        return False, "", "empty candidate"
    # Strip one optional Markdown fence if the model ignored protocol slightly.
    if body.startswith("```python") and body.endswith("```"):
        body = body[len("```python"): -3].strip()
    elif body.startswith("```") and body.endswith("```"):
        body = body[3:-3].strip()
    return True, body + "\n", "PASS"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    model_dir = root / "models" / args.model_id / "run_001"
    model_json = model_dir / "model.json"
    prompt_path = root / "frozen/implementation_prompt.txt"

    if not model_json.is_file():
        raise SystemExit(f"FAIL: unknown model_id or run not prepared: {args.model_id}")
    if not prompt_path.is_file():
        raise SystemExit("FAIL: frozen prompt missing")

    model = load_json(model_json)
    opencode_model = model["opencode_model"]
    ws = model_dir / "workspace"
    candidate = model_dir / "candidate" / "go_to_target.py"
    candidate.parent.mkdir(parents=True, exist_ok=True)

    # Prevent accidental contamination from a previous candidate.
    if candidate.exists():
        raise SystemExit(f"FAIL: immutable run already has candidate: {candidate}")

    prompt = prompt_path.read_text(encoding="utf-8")
    raw_path = model_dir / "opencode_raw.txt"
    before_gpu = gpu_snapshot()

    cmd = [
        "opencode", "run",
        "--standalone",
        "--agent", "generator",
        "--model", opencode_model,
        "--auto",
        "--title", f"NAV-001 STEP36A {args.model_id}",
        prompt,
    ]

    start = time.monotonic()
    try:
        p = subprocess.run(
            cmd,
            cwd=ws,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=args.timeout,
            check=False,
            env=os.environ.copy(),
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        raw = (exc.stdout or "") + "\n" + (exc.stderr or "")
        raw_path.write_text(raw, encoding="utf-8")
        result = {
            "model_id": args.model_id,
            "ollama_tag": model["ollama_tag"],
            "opencode_model": opencode_model,
            "provider_exit_code": None,
            "provider_timeout": True,
            "agent_duration_s": elapsed,
            "generation_protocol": "FAIL",
            "candidate_created": False,
            "classification": "PROVIDER_TIMEOUT",
            "gpu_before": before_gpu,
            "gpu_after": gpu_snapshot(),
        }
        (model_dir / "generation_result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return 2

    elapsed = time.monotonic() - start
    raw = p.stdout
    raw_path.write_text(raw, encoding="utf-8")

    ok, source, reason = extract_candidate(raw)
    if ok:
        candidate.write_text(source, encoding="utf-8")

    result = {
        "model_id": args.model_id,
        "ollama_tag": model["ollama_tag"],
        "opencode_model": opencode_model,
        "provider_exit_code": p.returncode,
        "provider_timeout": False,
        "agent_duration_s": elapsed,
        "generation_protocol": "PASS" if ok else "FAIL",
        "candidate_created": ok,
        "candidate_sha256": sha256(candidate) if ok else None,
        "protocol_detail": reason,
        "classification": (
            "CANDIDATE_CREATED"
            if ok and p.returncode == 0
            else "GENERATION_FORMAT_FAIL"
            if p.returncode == 0
            else "PROVIDER_OR_AGENT_FAIL"
        ),
        "gpu_before": before_gpu,
        "gpu_after": gpu_snapshot(),
    }
    (model_dir / "generation_result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )

    print("=== STEP36A MODEL GENERATION ===")
    print(f"MODEL_ID={args.model_id}")
    print(f"OLLAMA_TAG={model['ollama_tag']}")
    print(f"OPENCODE_MODEL={opencode_model}")
    print(f"EXIT_CODE={p.returncode}")
    print(f"AGENT_DURATION_S={elapsed:.3f}")
    print(f"GENERATION_PROTOCOL={'PASS' if ok else 'FAIL'}")
    print(f"CANDIDATE_CREATED={'YES' if ok else 'NO'}")
    print(f"RESULT={model_dir / 'generation_result.json'}")
    if ok:
        print(f"CANDIDATE={candidate}")
    return 0 if (p.returncode == 0 and ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
