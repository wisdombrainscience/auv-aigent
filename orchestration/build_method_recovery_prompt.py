#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


TARGET_CLASS = "GoToTargetModule"
METHODS = ("check_preconditions", "build_result")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def get_class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == TARGET_CLASS
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one top-level {TARGET_CLASS}, got {len(matches)}"
        )
    return matches[0]


def find_method(cls: ast.ClassDef, name: str):
    matches = [
        n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one method {name}, got {len(matches)}")
    return matches[0]


def segment(lines: list[str], node: ast.AST) -> str:
    return "".join(lines[node.lineno - 1: node.end_lineno])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--failure-package", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument(
        "--contract",
        type=Path,
        default=Path("specs/NAV-001-IMPLEMENTATION-CONTRACT.yaml"),
    )
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()
    failure_path = (
        args.failure_package
        if args.failure_package.is_absolute()
        else project / args.failure_package
    ).resolve()
    contract_path = (
        args.contract if args.contract.is_absolute()
        else project / args.contract
    ).resolve()
    output = args.output.expanduser().resolve()

    for label, path in (
        ("candidate", candidate),
        ("failure package", failure_path),
        ("contract", contract_path),
    ):
        if not path.is_file():
            raise SystemExit(f"{label} missing: {path}")

    source = candidate.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    cls = get_class(tree)

    method_sources = {
        name: segment(lines, find_method(cls, name))
        for name in METHODS
    }

    import_nodes = [
        n for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    imports = "".join(segment(lines, n) for n in import_nodes).strip()

    contract = read_json(contract_path)
    failure = read_json(failure_path)

    scoped_contract = {
        "constructor_contract": contract.get("constructor_contract"),
        "error_semantics": contract.get("error_semantics"),
        "success_contract": contract.get("success_contract"),
        "runtime_failure_contract": contract.get("runtime_failure_contract"),
        "verification_rule": contract.get("verification_rule"),
        "forbidden": contract.get("forbidden"),
    }

    parts = [
        "You are NAV-001 Recovery Agent 003, run_002.",
        "",
        "The previous run produced a malformed unified diff.",
        "That was a GENERATION_FORMAT failure, not a semantic recovery result.",
        "",
        "This retry uses METHOD-SCOPED REPLACEMENT instead of unified diff.",
        "",
        "You may replace ONLY these two methods:",
        "- check_preconditions",
        "- build_result",
        "",
        "Do NOT modify execute, __init__, imports, tests, spec, verifier, core APIs,",
        "adapter APIs, evidence, acceptance criteria, or any other method.",
        "",
        "Fix ONLY the failures in the supplied failure package.",
        "Preserve all currently passing behavior.",
        "",
        "Important semantics:",
        "- localization invalid BEFORE execute => PRECONDITION_FAILED",
        "- localization lost DURING execute => LOCALIZATION_LOST",
        "- position_error_m in module result is diagnostic only",
        "- independent verifier still recomputes position error from telemetry",
        "",
        "Do not ask questions.",
        "Do not call tools.",
        "Do not claim tests passed.",
        "",
        "Return EXACTLY two method blocks and no other text:",
        "",
        "<<<METHOD:check_preconditions>>>",
        "<complete replacement method>",
        "<<<END_METHOD>>>",
        "<<<METHOD:build_result>>>",
        "<complete replacement method>",
        "<<<END_METHOD>>>",
        "",
        "Method names, async/sync kind, parameters, and signatures must stay unchanged.",
        "",
        "===== RELEVANT FROZEN CONTRACT =====",
        json.dumps(scoped_contract, indent=2, ensure_ascii=False),
        "",
        "===== STRUCTURED FAILURE PACKAGE =====",
        json.dumps(failure, indent=2, ensure_ascii=False),
        "",
        "===== CURRENT MODULE IMPORTS =====",
        imports,
        "",
        "===== CURRENT check_preconditions =====",
        method_sources["check_preconditions"],
        "",
        "===== CURRENT build_result =====",
        method_sources["build_result"],
        "",
        "===== FINAL TASK =====",
        "Return only the two complete replacement methods in the required protocol.",
    ]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")

    print("METHOD RECOVERY PROMPT: PASS")
    print(f"OUTPUT={output}")
    print(f"BYTES={output.stat().st_size}")
    print("METHODS=check_preconditions,build_result")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
