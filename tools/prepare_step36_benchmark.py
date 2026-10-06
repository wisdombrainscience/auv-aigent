#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def compact_api(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef):
                lines.append(f"class {node.name}:")
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        kind = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                        args = []
                        for arg in child.args.args:
                            args.append(arg.arg)
                        if child.args.vararg:
                            args.append("*" + child.args.vararg.arg)
                        for arg in child.args.kwonlyargs:
                            args.append(arg.arg)
                        if child.args.kwarg:
                            args.append("**" + child.args.kwarg.arg)
                        lines.append(f"    {kind} {child.name}({', '.join(args)}): ...")
            else:
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                lines.append(f"{kind} {node.name}(...): ...")
    return "\n".join(lines)


def opencode_models() -> list[str]:
    try:
        p = subprocess.run(
            ["opencode", "models", "ollama"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
    except Exception:
        return []
    found = []
    for line in p.stdout.splitlines():
        line = line.strip()
        m = re.search(r"(ollama/[^\s]+)", line)
        if m:
            found.append(m.group(1).strip())
    return sorted(set(found))


def resolve_model(entry: dict, discovered: list[str]) -> str:
    for candidate in entry["opencode_candidates"]:
        if candidate in discovered:
            return candidate
    # Known working fallback from project history / direct provider naming.
    return entry["opencode_candidates"][0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    config = load_json(project / "benchmark/step36_models.json")
    contract_path = project / "specs/NAV-001-IMPLEMENTATION-CONTRACT.yaml"
    status_path = project / "reports/pipeline_v2/step19_30_status.json"

    if not contract_path.is_file():
        raise SystemExit("FAIL: implementation contract missing")
    if not status_path.is_file():
        raise SystemExit("FAIL: acceptance status missing")

    status = load_json(status_path)
    if status.get("candidate_status") != "ACCEPTED":
        raise SystemExit("FAIL: NAV-001 accepted candidate missing")
    accepted_hash = status["candidate_sha256"]

    # Verify all protected hashes before freezing benchmark context.
    contract = load_json(contract_path)
    for rel, expected in contract["protected_hashes"].items():
        p = project / rel
        if not p.is_file() or sha256(p) != expected:
            raise SystemExit(f"FAIL: protected file changed: {rel}")

    discovered = opencode_models()

    root.mkdir(parents=True, exist_ok=True)
    frozen = root / "frozen"
    frozen.mkdir(parents=True, exist_ok=True)

    # Freeze only specification/API context. Never copy accepted implementation.
    context_parts = [
        "# FROZEN NAV-001 IMPLEMENTATION CONTRACT\n",
        contract_path.read_text(encoding="utf-8"),
        "\n# BASE CAPABILITY API\n",
        compact_api(project / "src/core/base_capability.py"),
        "\n# UAV ADAPTER API\n",
        compact_api(project / "src/adapters/base_uav_adapter.py"),
        "\n# RESULT API\n",
        compact_api(project / "src/core/result.py"),
        "\n# FAILURE / ERROR API\n",
        (project / "src/core/errors.py").read_text(encoding="utf-8"),
    ]
    context = "\n".join(context_parts)

    prompt = f"""You are the Implementation Role for frozen task NAV-001.

Implement exactly one Python file:
src/capabilities/navigation/go_to_target.py

You MUST obey the frozen contract and API context below.

Critical rules:
- Do not modify specification, verifier, tests, evidence, core APIs, or adapter APIs.
- Do not import PX4, MAVSDK, pymavlink, Gazebo, ROS, or simulator-specific packages.
- Do not arm, disarm, take off, land, or control simulator lifecycle.
- Use only the provided adapter abstraction.
- Preconditions: connected, airborne, localization_valid.
- Initial invalid localization is PRECONDITION_FAILED.
- Localization lost after execution begins is LOCALIZATION_LOST.
- Unsupported module frame is UNSUPPORTED_FRAME.
- Constructor must support GoToTargetModule(adapter) and
  GoToTargetModule(adapter, poll_interval_s=0.001).
- Success result data must include target_position, final_position,
  position_error_m, elapsed_time_s.
- position_error_m is diagnostic; the external verifier remains authoritative.
- Produce no prose outside the file protocol.

Return EXACTLY:
<<<FILE:src/capabilities/navigation/go_to_target.py>>>
<complete Python source>
<<<END_FILE>>>

FROZEN CONTEXT
==============
{context}
"""

    prompt_path = frozen / "implementation_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    (frozen / "benchmark_contract.json").write_text(
        json.dumps(
            {
                "task": "NAV-001",
                "accepted_reference_sha256": accepted_hash,
                "accepted_source_exposed_to_models": False,
                "prompt_sha256": sha256(prompt_path),
                "protected_hashes": contract["protected_hashes"],
                "policy": {
                    "first_attempts_per_model": 1,
                    "same_prompt": True,
                    "same_validator": True,
                    "sitl_in_phase_36a": False,
                    "acceptance_criteria_changed": False,
                },
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    resolved = []
    for entry in config["models"]:
        item = dict(entry)
        item["opencode_model"] = resolve_model(entry, discovered)
        resolved.append(item)

        model_dir = root / "models" / entry["id"] / "run_001"
        ws = model_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

        # Prefer the previously proven text-only generator configuration from
        # Recovery-002. Fall back to the project OpenCode config only if that
        # immutable agent workspace is unavailable. CLI --model overrides model.
        proven_ws = (
            Path.home()
            / "UAV"
            / "st0_agent_jobs"
            / "NAV-001"
            / "recovery_002"
            / "workspace"
        )
        config_source = proven_ws if proven_ws.is_dir() else project

        if (config_source / "opencode.jsonc").is_file():
            shutil.copy2(config_source / "opencode.jsonc", ws / "opencode.jsonc")
        elif (project / "opencode.jsonc").is_file():
            shutil.copy2(project / "opencode.jsonc", ws / "opencode.jsonc")

        if (config_source / ".opencode").is_dir():
            shutil.copytree(
                config_source / ".opencode",
                ws / ".opencode",
                dirs_exist_ok=True,
            )
        elif (project / ".opencode").is_dir():
            shutil.copytree(
                project / ".opencode",
                ws / ".opencode",
                dirs_exist_ok=True,
            )

        generator_profile = ws / ".opencode" / "agents" / "generator.md"
        if not generator_profile.is_file():
            raise SystemExit(
                "FAIL: proven text-only generator profile not found. "
                "Expected recovery_002/workspace/.opencode/agents/generator.md"
            )

        (model_dir / "model.json").write_text(
            json.dumps(item, indent=2) + "\n", encoding="utf-8"
        )

    (root / "resolved_models.json").write_text(
        json.dumps(
            {
                "discovered_opencode_models": discovered,
                "models": resolved,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print("=== NAV-001 STEP 36A PREPARATION ===")
    print("PROTECTED_HASHES=PASS")
    print("ACCEPTED_SOURCE_EXPOSED_TO_MODELS=NO")
    print(f"PROMPT_SHA256={sha256(prompt_path)}")
    print(f"UNIQUE_MODEL_CONFIGS={len(resolved)}")
    print(f"DISCOVERED_OPENCODE_MODELS={len(discovered)}")
    for item in resolved:
        print(f"{item['id']}={item['opencode_model']}")
    print(f"ROOT={root}")
    print("STEP36A_PREPARATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
