from pathlib import Path
import json

ROOT = Path.home() / "UAV/st0_agent_jobs/NAV-001/step36_benchmark_v3"

MODELS = [
    "Q3_4B_AGENT_8K",
    "Q3_4B_AGENT_32K",
    "Q3_4B_INSTRUCT",
    "Q25_CODER_3B_32K",
    "Q25_CODER_3B_INSTRUCT",
]

rows = []
terminal = 0
semantic = 0

for model in MODELS:
    root = ROOT / "models" / model
    scored = root / "run_001_scored/execution_result.json"
    compat = root / "terminal_status.json"

    if scored.is_file():
        d = json.loads(scored.read_text())
        semantic_attempt = bool(
            d.get("semantic_model_attempt", False)
            or (
                d.get("exit_code") == 0
                and not d.get("timed_out", False)
            )
        )

        rows.append({
            "model_id": model,
            "terminal": True,
            "classification": d.get("classification"),
            "semantic_model_attempt": semantic_attempt,
            "generation_protocol_pass":
                bool(d.get("generation_protocol_pass", False)),
            "elapsed_s": d.get("elapsed_s"),
        })
        terminal += 1
        semantic += int(semantic_attempt)

    elif compat.is_file():
        d = json.loads(compat.read_text())
        rows.append(d)
        terminal += int(bool(d.get("terminal")))
        semantic += int(bool(d.get("semantic_model_attempt")))

    else:
        rows.append({
            "model_id": model,
            "terminal": False,
            "classification": "PENDING"
        })

result = {
    "task": "NAV-001",
    "step": "36A",
    "benchmark_version": "V3",
    "scope": "SIMULATION_ONLY",
    "models_expected": len(MODELS),
    "models_terminal": terminal,
    "semantic_attempts": semantic,
    "phase_a_complete": terminal == len(MODELS),
    "rows": rows,
}

out = ROOT / "results"
out.mkdir(parents=True, exist_ok=True)

(out / "phase_a_final.json").write_text(
    json.dumps(result, indent=2) + "\n"
)

print(f"MODELS_TERMINAL={terminal}/{len(MODELS)}")
print(f"SEMANTIC_ATTEMPTS={semantic}/{len(MODELS)}")
print(
    "PHASE_A_COMPLETE="
    + ("YES" if result["phase_a_complete"] else "NO")
)

for r in rows:
    print(
        r["model_id"],
        "->",
        r.get("classification")
    )
