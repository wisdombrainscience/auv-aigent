#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, time
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

def extract_candidate(raw: str):
    starts = [m.start() for m in re.finditer(re.escape(FILE_START), raw)]
    ends = [m.start() for m in re.finditer(re.escape(FILE_END), raw)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        return False, "", f"marker count start={len(starts)} end={len(ends)}"
    body = raw[starts[0] + len(FILE_START):ends[0]].strip()
    if body.startswith("```python") and body.endswith("```"):
        body = body[len("```python"):-3].strip()
    elif body.startswith("```") and body.endswith("```"):
        body = body[3:-3].strip()
    if not body:
        return False, "", "empty candidate"
    return True, body + "\n", "PASS"

def gpu_snapshot():
    p = subprocess.run(
        ["nvidia-smi","--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu","--format=csv,noheader,nounits"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    return p.stdout.strip()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    run = root / "models" / args.model_id / "run_002_semantic"
    model_path = run / "model.json"
    prompt_path = root / "frozen/implementation_prompt.txt"
    ws = run / "workspace"

    if not model_path.is_file():
        raise SystemExit("FAIL: run_002_semantic is not prepared")
    if not (ws / ".opencode/agents/generator.md").is_file():
        raise SystemExit("FAIL: generator agent profile missing")
    if not (ws / "opencode.jsonc").is_file():
        raise SystemExit("FAIL: per-model OpenCode config missing")

    candidate = run / "candidate/go_to_target.py"
    if candidate.exists() or (run / "generation_result.json").exists():
        raise SystemExit("FAIL: semantic run already has evidence; do not overwrite")
    candidate.parent.mkdir(parents=True, exist_ok=True)

    model = load_json(model_path)
    prompt = prompt_path.read_text(encoding="utf-8")
    raw_path = run / "opencode_raw.txt"

    cmd = [
        "opencode","run","--standalone",
        "--agent","generator",
        "--model","ollama/bench-model",
        "--auto",
        "--title",f"NAV-001 STEP36A {args.model_id} semantic",
        prompt,
    ]

    gpu_before = gpu_snapshot()
    start = time.monotonic()
    try:
        p = subprocess.run(
            cmd, cwd=ws, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=args.timeout, check=False, env=os.environ.copy()
        )
        elapsed = time.monotonic() - start
        raw = p.stdout or ""
        timeout = False
        exit_code = p.returncode
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        raw = (exc.stdout or "") + "\n" + (exc.stderr or "")
        timeout = True
        exit_code = None

    raw_path.write_text(raw, encoding="utf-8")
    ok, source, detail = extract_candidate(raw)
    if ok:
        candidate.write_text(source, encoding="utf-8")

    if timeout:
        classification = "PROVIDER_TIMEOUT"
    elif exit_code != 0:
        classification = "PROVIDER_OR_AGENT_FAIL"
    elif not ok:
        classification = "GENERATION_FORMAT_FAIL"
    else:
        classification = "CANDIDATE_CREATED"

    result = {
        "model_id": args.model_id,
        "ollama_tag": model["ollama_tag"],
        "opencode_model": "ollama/bench-model",
        "semantic_model_attempt": (not timeout and exit_code == 0),
        "provider_exit_code": exit_code,
        "provider_timeout": timeout,
        "agent_duration_s": elapsed,
        "generation_protocol": "PASS" if ok else "FAIL",
        "candidate_created": ok,
        "candidate_sha256": sha256(candidate) if ok else None,
        "protocol_detail": detail,
        "classification": classification,
        "gpu_before": gpu_before,
        "gpu_after": gpu_snapshot(),
    }
    (run / "generation_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print("=== STEP36A V2 MODEL GENERATION ===")
    print(f"MODEL_ID={args.model_id}")
    print(f"OLLAMA_TAG={model['ollama_tag']}")
    print("OPENCODE_MODEL=ollama/bench-model")
    print(f"EXIT_CODE={exit_code}")
    print(f"AGENT_DURATION_S={elapsed:.3f}")
    print(f"GENERATION_PROTOCOL={'PASS' if ok else 'FAIL'}")
    print(f"CANDIDATE_CREATED={'YES' if ok else 'NO'}")
    print(f"CLASSIFICATION={classification}")
    print(f"RESULT={run / 'generation_result.json'}")
    if ok:
        print(f"CANDIDATE={candidate}")
    return 0 if (exit_code == 0 and ok) else 1

if __name__ == "__main__":
    raise SystemExit(main())
