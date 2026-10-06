#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, time
from pathlib import Path

DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"
FILE_START = "<<<FILE:src/capabilities/navigation/go_to_target.py>>>"
FILE_END = "<<<END_FILE>>>"

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def execute(cmd, ws: Path, timeout_s: int):
    env = os.environ.copy()

    # Critical fix: keep OpenCode's project-discovery environment aligned with cwd.
    env["PWD"] = str(ws)

    start = time.monotonic()
    try:
        p = subprocess.run(
            cmd,
            cwd=ws,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        return p.returncode, False, time.monotonic() - start, p.stdout or ""
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "") + "\n" + (exc.stderr or "")
        return None, True, time.monotonic() - start, raw

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    root = args.root.expanduser().resolve()
    model_root = root / "models" / args.model_id

    run_name = "smoke_envfix" if args.smoke else "run_001_scored"
    run = model_root / run_name
    ws = run / "workspace"
    manifest_path = run / "run_manifest.json"

    if not manifest_path.is_file():
        raise SystemExit(f"FAIL: run workspace missing: {run}")

    result_path = run / "execution_result.json"
    if result_path.exists():
        raise SystemExit("REFUSE: execution evidence already exists")

    # A scored run is forbidden until the fixed smoke has passed.
    if not args.smoke:
        smoke_result = model_root / "smoke_envfix/execution_result.json"
        if not smoke_result.is_file():
            raise SystemExit("FAIL: smoke_envfix has not been run")
        smoke = load_json(smoke_result)
        transport_pass = (
            smoke.get("exit_code") == 0
            and smoke.get("timed_out") is False
        )
        if not transport_pass:
            raise SystemExit(
                "FAIL: smoke transport/provider gate did not PASS; scored run forbidden"
            )

    # File-based agent is intentionally used because it was already proven manually.
    agent_file = ws / ".opencode/agents/generator.md"
    if not agent_file.is_file():
        raise SystemExit(f"FAIL: generator agent file missing: {agent_file}")

    meta = load_json(manifest_path)

    if args.smoke:
        prompt = f"Return exactly: {args.model_id}_ENVFIX_OK"
        timeout_s = 120
    else:
        prompt = (root / "frozen/implementation_prompt.txt").read_text(encoding="utf-8")
        timeout_s = args.timeout

    # Keep the command identical to the manually proven invocation.
    # In particular: no --title and explicit --agent generator.
    cmd = [
        "opencode", "run",
        "--standalone",
        "--agent", "generator",
        "--model", "ollama/bench-model",
        "--auto",
        prompt,
    ]

    exit_code, timed_out, elapsed, raw = execute(cmd, ws, timeout_s)
    raw_path = run / "opencode_raw.txt"
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
        "runner_fix": {
            "child_pwd_equals_workspace": True,
            "title_flag_removed": True,
            "file_based_agent": True,
        },
    }

    if args.smoke:
        token = f"{args.model_id}_ENVFIX_OK"
        ok = (not timed_out and exit_code == 0 and token in raw)
        result["smoke_pass"] = ok
        result["classification"] = (
            "SMOKE_PASS" if ok
            else "SMOKE_TIMEOUT" if timed_out
            else "SMOKE_FAIL"
        )
    else:
        start_count = raw.count(FILE_START)
        end_count = raw.count(FILE_END)
        result["start_markers"] = start_count
        result["end_markers"] = end_count
        if timed_out:
            cls = "TIMEOUT"
        elif exit_code != 0:
            cls = "PROVIDER_OR_AGENT_FAIL"
        elif start_count == 1 and end_count == 1:
            cls = "GENERATION_PROTOCOL_PASS"
        else:
            cls = "GENERATION_FORMAT_FAIL"
        result["classification"] = cls
        result["semantic_model_attempt"] = (not timed_out and exit_code == 0)
        result["generation_protocol_pass"] = (cls == "GENERATION_PROTOCOL_PASS")

    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print("=== STEP36 V3 FINAL RUNNER ===")
    print(f"MODEL_ID={args.model_id}")
    print(f"RUN={run_name}")
    print(f"BENCHMARK_TAG={meta['benchmark_tag']}")
    print("FILE_BASED_AGENT=YES")
    print("CHILD_PWD_EQUALS_WORKSPACE=YES")
    print("TITLE_FLAG=REMOVED")
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
    print(f"RESULT={result_path}")

    return 0 if (result.get("smoke_pass") or result.get("generation_protocol_pass")) else 1

if __name__ == "__main__":
    raise SystemExit(main())
