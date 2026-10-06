#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_contract(path: Path) -> dict:
    # Contract file is JSON syntax, which is also valid YAML 1.2.
    return json.loads(path.read_text(encoding="utf-8"))


def failure_code_members(errors_file: Path) -> set[str]:
    tree = ast.parse(errors_file.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "FailureCode":
            return {
                item.targets[0].id
                for item in node.body
                if isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
            }
    return set()


def candidate_failure_code_refs(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "FailureCode"
        ):
            out.add(node.attr)
    return out


def candidate_import_roots(tree: ast.AST) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0].lower())
    return roots


def candidate_called_methods(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.add(node.func.attr.lower())
    return out


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> dict:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return {"command": cmd, "exit_code": p.returncode, "output": p.stdout}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument(
        "--contract",
        type=Path,
        default=Path("specs/NAV-001-IMPLEMENTATION-CONTRACT.yaml"),
    )
    ap.add_argument("--targeted-tests", action="store_true")
    ap.add_argument("--full-tests", action="store_true")
    ap.add_argument(
        "--report",
        type=Path,
        default=Path("reports/pipeline_v2/candidate_validation.json"),
    )
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()
    contract_path = (
        args.contract if args.contract.is_absolute() else project / args.contract
    ).resolve()
    report_path = (
        args.report if args.report.is_absolute() else project / args.report
    ).resolve()

    result = {"candidate": str(candidate), "gates": {}, "overall": "FAIL"}

    def gate(name: str, ok: bool, details=None):
        result["gates"][name] = {"status": "PASS" if ok else "FAIL"}
        if details is not None:
            result["gates"][name]["details"] = details
        if not ok:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"{name}: FAIL")
            if details:
                print(details if isinstance(details, str) else json.dumps(details, indent=2))
            raise SystemExit(1)
        print(f"{name}: PASS")

    gate("G0_PROJECT", project.is_dir(), str(project))
    gate("G1_CONTRACT", contract_path.is_file(), str(contract_path))
    contract = load_contract(contract_path)
    gate("G2_CANDIDATE_EXISTS", candidate.is_file(), str(candidate))

    protected_details = {}
    protected_ok = True
    for rel, expected in contract["protected_hashes"].items():
        p = project / rel
        actual = sha256(p) if p.is_file() else None
        protected_details[rel] = {"expected": expected, "actual": actual}
        if actual != expected:
            protected_ok = False
    gate("G3_PROTECTED_HASHES", protected_ok, protected_details)

    try:
        py_compile.compile(str(candidate), doraise=True)
        source = candidate.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as exc:
        gate("G4_SYNTAX_AST", False, repr(exc))
    gate("G4_SYNTAX_AST", True)

    top_classes = [
        n.name for n in tree.body if isinstance(n, ast.ClassDef)
    ]
    gate(
        "G5_STRUCTURE",
        top_classes.count("GoToTargetModule") == 1,
        {"top_level_classes": top_classes},
    )

    imports = candidate_import_roots(tree)
    forbidden_imports = {x.lower() for x in contract["forbidden"]["imports"]}
    bad_imports = sorted(imports & forbidden_imports)
    gate("G6_PLATFORM_IMPORT_BOUNDARY", not bad_imports, {"bad_imports": bad_imports})

    calls = candidate_called_methods(tree)
    forbidden_actions = {x.lower() for x in contract["forbidden"]["vehicle_actions"]}
    bad_actions = sorted(calls & forbidden_actions)
    gate("G7_FORBIDDEN_ACTION_BOUNDARY", not bad_actions, {"bad_actions": bad_actions})

    allowed_codes = failure_code_members(project / "src/core/errors.py")
    used_codes = candidate_failure_code_refs(tree)
    unknown_codes = sorted(used_codes - allowed_codes)
    gate(
        "G8_FAILURE_CODE_API",
        not unknown_codes,
        {
            "used": sorted(used_codes),
            "allowed": sorted(allowed_codes),
            "unknown": unknown_codes,
        },
    )

    with tempfile.TemporaryDirectory(prefix="nav001_candidate_") as td:
        ws = Path(td) / "workspace"

        def ignore(path, names):
            ignored = set()
            for name in names:
                if name in {".git", ".venv", "runs", "__pycache__"}:
                    ignored.add(name)
                if name.endswith(".pyc"):
                    ignored.add(name)
            return ignored

        shutil.copytree(project, ws, ignore=ignore)
        target_rel = Path("src/capabilities/navigation/go_to_target.py")
        target = ws / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)

        project_python = project / ".venv/bin/python"
        python_bin = str(project_python) if project_python.is_file() else "python"
        env = {
            **os.environ,
            "PYTHONPATH": str(ws),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }

        import_code = (
            "from src.capabilities.navigation.go_to_target import GoToTargetModule;"
            "import inspect;"
            "print(inspect.signature(GoToTargetModule))"
        )
        import_result = run([python_bin, "-c", import_code], ws, env)
        gate(
            "G9_IMPORT",
            import_result["exit_code"] == 0,
            import_result,
        )

        ctor_code = (
            "from src.capabilities.navigation.go_to_target import GoToTargetModule;"
            "from tests.unit.test_manual_goto_target import FakeAdapter;"
            "GoToTargetModule(FakeAdapter());"
            "GoToTargetModule(FakeAdapter(), poll_interval_s=0.001);"
            "print('CONSTRUCTOR_OK')"
        )
        ctor_result = run([python_bin, "-c", ctor_code], ws, env)
        gate(
            "G10_CONSTRUCTOR",
            ctor_result["exit_code"] == 0,
            ctor_result,
        )

        if args.targeted_tests or args.full_tests:
            targeted = run(
                [
                    python_bin,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/unit/test_manual_goto_target.py",
                ],
                ws,
                env,
            )
            gate(
                "G11_TARGETED_TESTS",
                targeted["exit_code"] == 0,
                targeted,
            )

        if args.full_tests:
            full = run([python_bin, "-m", "pytest", "-q"], ws, env)
            gate("G12_FULL_TESTS", full["exit_code"] == 0, full)

    result["overall"] = "PASS"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("OVERALL: PASS")
    print(f"REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
