from __future__ import annotations

from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / ".opencode" / "agents"
EXPECTED = {
    "implementation.md": "src/capabilities/navigation/**",
    "test-generation.md": "tests/unit/generated/**",
    "recovery.md": "src/capabilities/navigation/**",
}
PROTECTED = {
    "specs/**",
    "tests/acceptance/**",
    "src/verification/**",
    "runs/**",
}


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated YAML frontmatter")
    return yaml.safe_load(text[4:end]) or {}


def last_edit_effect(rules: list[dict], resource: str) -> str | None:
    # This checker focuses on explicit exact resources used in the profiles.
    effect = None
    for rule in rules:
        if rule.get("action") == "edit" and rule.get("resource") in {"*", resource}:
            effect = rule.get("effect")
    return effect


def main() -> int:
    failures: list[str] = []
    for filename, writable in EXPECTED.items():
        path = AGENT_DIR / filename
        if not path.exists():
            failures.append(f"{filename}: missing")
            continue
        try:
            data = frontmatter(path)
        except Exception as exc:
            failures.append(f"{filename}: {exc}")
            continue

        if data.get("mode") != "primary":
            failures.append(f"{filename}: mode must be primary")
        if data.get("model") != "ollama/qwen2.5-coder:3b":
            failures.append(f"{filename}: unexpected model")
        rules = data.get("permissions") or []
        if last_edit_effect(rules, "*") != "deny":
            failures.append(f"{filename}: edit '*' must default deny")
        if last_edit_effect(rules, writable) not in {"allow", "ask"}:
            failures.append(f"{filename}: writable scope not enabled")
        for protected in PROTECTED:
            # Because '*' is denied and no protected path is later allowed, it remains denied.
            if any(
                r.get("action") == "edit"
                and r.get("resource") == protected
                and r.get("effect") in {"allow", "ask"}
                for r in rules
            ):
                failures.append(f"{filename}: protected scope enabled: {protected}")

    if failures:
        print("AGENT PROFILE CHECK: FAIL")
        for item in failures:
            print(f" - {item}")
        return 1

    print("AGENT PROFILE CHECK: PASS")
    for filename in EXPECTED:
        print(f" - {filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
