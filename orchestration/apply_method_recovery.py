#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import textwrap
from pathlib import Path


TARGET_CLASS = "GoToTargetModule"
ALLOWED_METHODS = ("check_preconditions", "build_result")

BLOCK_RE = re.compile(
    r"^<<<METHOD:([A-Za-z_][A-Za-z0-9_]*)>>>\s*\n"
    r"(.*?)"
    r"^<<<END_METHOD>>>\s*$",
    re.MULTILINE | re.DOTALL,
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def get_class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == TARGET_CLASS
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one top-level {TARGET_CLASS}, got {len(matches)}"
        )
    return matches[0]


def methods_by_name(cls: ast.ClassDef) -> dict:
    out = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def signature_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return json.dumps(
        {
            "kind": type(node).__name__,
            "args": ast.dump(node.args, include_attributes=False),
            "returns": ast.dump(node.returns, include_attributes=False)
            if node.returns is not None else None,
            "decorators": [
                ast.dump(d, include_attributes=False)
                for d in node.decorator_list
            ],
        },
        sort_keys=True,
    )


def parse_replacement(name: str, block: str):
    dedented = textwrap.dedent(block).strip() + "\n"
    tree = ast.parse(dedented)
    nodes = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(tree.body) != 1 or len(nodes) != 1:
        raise ValueError(
            f"{name}: replacement must contain exactly one function/method definition"
        )
    node = nodes[0]
    if node.name != name:
        raise ValueError(
            f"{name}: replacement defines {node.name!r}, expected {name!r}"
        )
    normalized = textwrap.indent(dedented, "    ")
    return node, normalized


def parse_blocks(text: str) -> dict[str, str]:
    matches = list(BLOCK_RE.finditer(text))
    if not matches:
        raise ValueError("no METHOD blocks found")

    outside = []
    last = 0
    for m in matches:
        outside.append(text[last:m.start()])
        last = m.end()
    outside.append(text[last:])
    if any(chunk.strip() for chunk in outside):
        raise ValueError("unexpected text outside METHOD blocks")

    blocks = {}
    for m in matches:
        name = m.group(1)
        if name in blocks:
            raise ValueError(f"duplicate METHOD block: {name}")
        blocks[name] = m.group(2)

    if set(blocks) != set(ALLOWED_METHODS):
        raise ValueError(
            f"method set mismatch: got={sorted(blocks)} "
            f"expected={sorted(ALLOWED_METHODS)}"
        )
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-candidate", type=Path, required=True)
    ap.add_argument("--response", type=Path, required=True)
    ap.add_argument("--output-candidate", type=Path, required=True)
    ap.add_argument("--expected-base-sha256")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    base = args.base_candidate.expanduser().resolve()
    response = args.response.expanduser().resolve()
    output = args.output_candidate.expanduser().resolve()
    report = args.report.expanduser().resolve() if args.report else None

    if not base.is_file():
        raise SystemExit(f"base candidate missing: {base}")
    if not response.is_file():
        raise SystemExit(f"response missing: {response}")

    actual_hash = sha256(base)
    if args.expected_base_sha256 and actual_hash != args.expected_base_sha256:
        raise SystemExit(
            "BASE HASH FAIL: "
            f"expected={args.expected_base_sha256} actual={actual_hash}"
        )

    source = base.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    cls = get_class(tree)
    method_map = methods_by_name(cls)

    originals = {}
    for name in ALLOWED_METHODS:
        nodes = method_map.get(name, [])
        if len(nodes) != 1:
            raise SystemExit(
                f"base candidate must contain exactly one {name}, got {len(nodes)}"
            )
        originals[name] = nodes[0]

    try:
        blocks = parse_blocks(response.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"RESPONSE PROTOCOL: FAIL: {exc}")

    replacements = {}
    metadata = {}

    for name in ALLOWED_METHODS:
        try:
            new_node, normalized = parse_replacement(name, blocks[name])
        except Exception as exc:
            raise SystemExit(f"METHOD PARSE: FAIL: {exc}")

        old_fp = signature_fingerprint(originals[name])
        new_fp = signature_fingerprint(new_node)
        if old_fp != new_fp:
            raise SystemExit(
                f"SIGNATURE GATE: FAIL for {name}; recovery may not change method signature"
            )

        replacements[name] = normalized
        metadata[name] = {
            "old_start_line": originals[name].lineno,
            "old_end_line": originals[name].end_lineno,
            "signature_preserved": True,
        }

    edits = sorted(
        (
            originals[name].lineno - 1,
            originals[name].end_lineno,
            replacements[name],
            name,
        )
        for name in ALLOWED_METHODS
    )

    new_lines = list(lines)
    for start, end, replacement, name in reversed(edits):
        new_lines[start:end] = replacement.splitlines(keepends=True)

    new_source = "".join(new_lines)

    try:
        new_tree = ast.parse(new_source)
    except SyntaxError as exc:
        raise SystemExit(
            f"FINAL AST GATE: FAIL line={exc.lineno} msg={exc.msg}"
        )

    new_cls = get_class(new_tree)
    new_methods = methods_by_name(new_cls)
    for name in ALLOWED_METHODS:
        nodes = new_methods.get(name, [])
        if len(nodes) != 1:
            raise SystemExit(
                f"FINAL STRUCTURE GATE: FAIL {name} count={len(nodes)}"
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(new_source, encoding="utf-8")

    result = {
        "status": "PASS",
        "base_candidate": str(base),
        "base_sha256": actual_hash,
        "response": str(response),
        "output_candidate": str(output),
        "output_sha256": sha256(output),
        "changed_methods": list(ALLOWED_METHODS),
        "method_metadata": metadata,
        "guarantee": (
            "deterministic replacement touched only the two permitted method spans"
        ),
    }

    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print("METHOD RESPONSE PROTOCOL: PASS")
    print("SIGNATURE GATE: PASS")
    print("FINAL AST GATE: PASS")
    print("METHOD-SCOPED APPLY: PASS")
    print("CHANGED_METHODS=check_preconditions,build_result")
    print(f"OUTPUT={output}")
    print(f"OUTPUT_SHA256={result['output_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
