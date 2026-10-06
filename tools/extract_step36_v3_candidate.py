#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
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

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()

    root = args.root.expanduser().resolve()
    run = root / "models" / args.model_id / "run_001_scored"
    result_path = run / "execution_result.json"
    raw_path = run / "opencode_raw.txt"
    if not result_path.is_file() or not raw_path.is_file():
        raise SystemExit("FAIL: scored run evidence missing")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("generation_protocol_pass"):
        raise SystemExit("FAIL: generation protocol did not pass; extraction forbidden")

    out = run / "candidate/go_to_target.py"
    if out.exists():
        raise SystemExit("REFUSE: candidate already exists")
    raw = raw_path.read_text(encoding="utf-8")
    start = raw.index(FILE_START) + len(FILE_START)
    end = raw.index(FILE_END, start)
    body = raw[start:end].strip()
    if body.startswith("```python") and body.endswith("```"):
        body = body[len("```python"):-3].strip()
    elif body.startswith("```") and body.endswith("```"):
        body = body[3:-3].strip()
    if not body:
        raise SystemExit("FAIL: extracted candidate is empty")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    meta = {"candidate_sha256": sha256(out), "manual_edit": False}
    (run / "candidate_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print("CANDIDATE_EXTRACTION=PASS")
    print(f"CANDIDATE={out}")
    print(f"SHA256={meta['candidate_sha256']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
