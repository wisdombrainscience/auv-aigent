#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re, subprocess, time, hashlib
from pathlib import Path

DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"
FILE_START = "<<<FILE:src/capabilities/navigation/go_to_target.py>>>"
FILE_END = "<<<END_FILE>>>"

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    root = args.root.expanduser().resolve()
    run_name = "smoke" if args.smoke else "run_001_scored"
    run = root / "models" / args.model_id / run_name
    ws = run / "workspace"
    meta_path = run / "run_manifest.json"
    if not meta_path.is_file():
        raise SystemExit(f"FAIL: workspace not prepared: {run}")
    if (run / "execution_result.json").exists():
        raise SystemExit("REFUSE: execution evidence already exists; do not overwrite")

    meta = load_json(meta_path)
    if args.smoke:
        prompt = f"Return exactly: {args.model_id}_SMOKE_OK"
        timeout = 120
    else:
        prompt = (root / "frozen/implementation_prompt.txt").read_text(encoding="utf-8")
        timeout = args.timeout

    cmd = [
        "opencode", "run", "--standalone",
        "--agent", "generator",
        "--model", "ollama/bench-model",
        "--auto",
        "--title", f"NAV-001 STEP36 V3 {args.model_id} {run_name}",
        prompt,
    ]
    raw_path = run / "opencode_raw.txt"
    start = time.monotonic()
    try:
        p = subprocess.run(
            cmd, cwd=ws, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False, env=os.environ.copy()
        )
        elapsed = time.monotonic() - start
        raw = p.stdout or ""
        exit_code = p.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        raw = (exc.stdout or "") + "\n" + (exc.stderr or "")
        exit_code = None
        timed_out = True

    raw_path.write_text(raw, encoding="utf-8")

    result = {
        "model_id": args.model_id,
        "benchmark_tag": meta["benchmark_tag"],
        "run_name": run_name,
        "smoke": args.smoke,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_s": elapsed,
        "raw_sha256": sha256(raw_path),
    }

    if args.smoke:
        token = f"{args.model_id}_SMOKE_OK"
        ok = (not timed_out and exit_code == 0 and token in raw)
        result["classification"] = "SMOKE_PASS" if ok else ("SMOKE_TIMEOUT" if timed_out else "SMOKE_FAIL")
        result["smoke_pass"] = ok
    else:
        start_count = raw.count(FILE_START)
        end_count = raw.count(FILE_END)
        result["start_markers"] = start_count
        result["end_markers"] = end_count
        semantic_reached = (not timed_out and exit_code == 0)
        result["semantic_model_attempt"] = semantic_reached

        if timed_out:
            classification = "TIMEOUT"
        elif exit_code != 0:
            classification = "PROVIDER_OR_AGENT_FAIL"
        elif start_count == 1 and end_count == 1:
            classification = "GENERATION_PROTOCOL_PASS"
        else:
            classification = "GENERATION_FORMAT_FAIL"

        result["classification"] = classification
        result["generation_protocol_pass"] = (classification == "GENERATION_PROTOCOL_PASS")
        result["first_attempt_acceptance"] = "PENDING_VALIDATION" if result["generation_protocol_pass"] else "FAIL"

    (run / "execution_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print("=== STEP36 V3 MODEL RUN ===")
    print(f"MODEL_ID={args.model_id}")
    print(f"RUN={run_name}")
    print(f"BENCHMARK_TAG={meta['benchmark_tag']}")
    print(f"EXIT_CODE={exit_code}")
    print(f"TIMED_OUT={timed_out}")
    print(f"ELAPSED_S={elapsed:.3f}")
    print(f"CLASSIFICATION={result['classification']}")
    if args.smoke:
        print(f"SMOKE_PASS={'YES' if result['smoke_pass'] else 'NO'}")
    else:
        print(f"START_MARKERS={result['start_markers']}")
        print(f"END_MARKERS={result['end_markers']}")
        print(f"GENERATION_PROTOCOL={'PASS' if result['generation_protocol_pass'] else 'FAIL'}")
    print(f"RESULT={run / 'execution_result.json'}")
    return 0 if (result.get("smoke_pass") or result.get("generation_protocol_pass")) else 1

if __name__ == "__main__":
    raise SystemExit(main())
