#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
import textwrap
from pathlib import Path

EXPECTED = ("check_preconditions", "build_result")

BLOCK_RE = re.compile(
    r"^<<<METHOD:([A-Za-z_][A-Za-z0-9_]*)>>>\s*\n"
    r"(.*?)"
    r"^<<<END_METHOD>>>\s*$",
    re.MULTILINE | re.DOTALL,
)

ESCAPE_RE = re.compile(r"\\.")


def parse_protocol(text: str) -> dict[str, str]:
    matches = list(BLOCK_RE.finditer(text))
    if not matches:
        raise ValueError("no METHOD blocks found")

    outside = []
    last = 0
    for match in matches:
        outside.append(text[last:match.start()])
        last = match.end()
    outside.append(text[last:])

    if any(chunk.strip() for chunk in outside):
        raise ValueError("unexpected text outside METHOD blocks")

    blocks: dict[str, str] = {}
    for match in matches:
        name = match.group(1)
        if name in blocks:
            raise ValueError(f"duplicate METHOD block: {name}")
        blocks[name] = match.group(2)

    if set(blocks) != set(EXPECTED):
        raise ValueError(
            f"method set mismatch: got={sorted(blocks)} "
            f"expected={sorted(EXPECTED)}"
        )
    return blocks


def parse_one_method(name: str, source: str) -> ast.AST:
    dedented = textwrap.dedent(source).strip() + "\n"
    tree = ast.parse(dedented)
    if len(tree.body) != 1:
        raise ValueError(f"{name}: expected one top-level definition")
    node = tree.body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError(f"{name}: block is not a function definition")
    if node.name != name:
        raise ValueError(f"{name}: defines {node.name!r}")
    return node


def conservative_unescape(block: str) -> tuple[str, list[str]]:
    escapes = sorted(set(ESCAPE_RE.findall(block)))
    allowed = {r'\"', r'\n'}
    unsupported = [esc for esc in escapes if esc not in allowed]
    if unsupported:
        raise ValueError(
            "unsupported escape sequence(s): " + ", ".join(unsupported)
        )

    normalized = block.replace(r'\"', '"').replace(r'\n', '\n')
    return normalized, escapes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    inp = args.input.expanduser().resolve()
    out = args.output.expanduser().resolve()
    report_path = args.report.expanduser().resolve()

    if not inp.is_file():
        raise SystemExit(f"input missing: {inp}")

    text = inp.read_text(encoding="utf-8")
    try:
        blocks = parse_protocol(text)
    except Exception as exc:
        raise SystemExit(f"PROTOCOL GATE: FAIL: {exc}")

    normalized_blocks: dict[str, str] = {}
    report = {
        "status": "PASS",
        "input": str(inp),
        "output": str(out),
        "methods": {},
    }

    for name in EXPECTED:
        raw = blocks[name]
        mode = "RAW"
        escapes: list[str] = []

        try:
            parse_one_method(name, raw)
            normalized = raw
        except (SyntaxError, ValueError) as raw_exc:
            try:
                normalized, escapes = conservative_unescape(raw)
                parse_one_method(name, normalized)
                mode = "CONSERVATIVE_UNESCAPE"
            except Exception as norm_exc:
                raise SystemExit(
                    f"{name}: NORMALIZATION FAIL\n"
                    f"raw_error={raw_exc}\n"
                    f"normalized_error={norm_exc}"
                )

        normalized_blocks[name] = textwrap.dedent(normalized).strip() + "\n"
        report["methods"][name] = {
            "mode": mode,
            "escapes_seen": escapes,
            "ast_parse": "PASS",
        }

    rendered = []
    for name in EXPECTED:
        rendered.append(f"<<<METHOD:{name}>>>")
        rendered.append(normalized_blocks[name].rstrip("\n"))
        rendered.append("<<<END_METHOD>>>")
    rendered_text = "\n".join(rendered) + "\n"

    # Re-parse the final protocol as a final deterministic check.
    final_blocks = parse_protocol(rendered_text)
    for name in EXPECTED:
        parse_one_method(name, final_blocks[name])

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered_text, encoding="utf-8")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("PROTOCOL GATE: PASS")
    for name in EXPECTED:
        info = report["methods"][name]
        print(
            f"{name}: AST=PASS MODE={info['mode']} "
            f"ESCAPES={info['escapes_seen']}"
        )
    print("NORMALIZED RESPONSE: PASS")
    print(f"OUTPUT={out}")
    print(f"REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
