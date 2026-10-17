#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from evaluation.common import PROJECT, stamp, write_json


def latest(root: Path, prefix: str) -> Path | None:
    items = sorted([p for p in root.glob(prefix + "_*") if p.is_dir()])
    return items[-1] if items else None


def read_csv(path: Path) -> list[dict[str,str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fmt_rate(value: Any, lo: Any = None, hi: Any = None) -> str:
    try:
        p = float(value) * 100
    except Exception:
        return "—"
    if lo not in (None,"") and hi not in (None,""):
        return f"{p:.1f}% ({float(lo)*100:.1f}–{float(hi)*100:.1f}%)"
    return f"{p:.1f}%"


def fmt_pm(mean: Any, sd: Any) -> str:
    try:
        return f"{float(mean):.3f} ± {float(sd):.3f}"
    except Exception:
        return "—"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build leader-ready evaluation tables from latest result folders")
    ap.add_argument("--report-dir", default=str(PROJECT / "reports" / "evaluation"))
    ap.add_argument("--comparative")
    ap.add_argument("--reliability")
    ap.add_argument("--longitudinal")
    args = ap.parse_args()
    root = Path(args.report_dir)
    comp = Path(args.comparative) if args.comparative else latest(root, "comparative")
    rel = Path(args.reliability) if args.reliability else latest(root, "reliability")
    lon = Path(args.longitudinal) if args.longitudinal else latest(root, "longitudinal")
    if not comp or not rel or not lon:
        raise SystemExit(f"missing result set(s): comparative={comp}, reliability={rel}, longitudinal={lon}")

    comp_rows = read_csv(comp / "comparative_summary.csv")
    rel_rows = read_csv(rel / "reliability_summary.csv")
    lon_rows = read_csv(lon / "longitudinal_milestones.csv")
    out = root / f"leader_tables_{stamp()}"
    out.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines += ["# Remote UAV – Evaluation Tables", "", "## 2.1 Comparative evaluation", "", "| Capability | N | Agree-P | Agree-F | Silent fail. | V-only lenient |", "|---|---:|---:|---:|---:|---:|"]
    for r in comp_rows:
        lines.append(f"| {r.get('Capability','')} | {r.get('N','')} | {r.get('Agree-P','')} | {r.get('Agree-F','')} | {r.get('Silent fail.','')} | {r.get('V-only lenient','')} |")
    lines += ["", "## 2.2 Reliability evaluation", "", "| Condition | n | Pass rate (95% CI) | e_pos mean ± SD (m) | e_pos p95 (m) | Expected-behavior rate |", "|---|---:|---:|---:|---:|---:|"]
    for r in rel_rows:
        lines.append(
            f"| {r.get('Condition','')} | {r.get('n','')} | {fmt_rate(r.get('Pass rate'),r.get('Pass 95% CI low'),r.get('Pass 95% CI high'))} | {fmt_pm(r.get('e_pos mean'),r.get('e_pos SD'))} | {r.get('e_pos p95') or '—'} | {fmt_rate(r.get('Expected-behavior rate'),r.get('Expected 95% CI low'),r.get('Expected 95% CI high'))} |"
        )
    lines += ["", "## 2.3 Longitudinal resource evaluation", "", "| t (min) | RSS (MB) | FDs | MAVSDK | Storage (MB) | p95 latency (ms) |", "|---:|---:|---:|---:|---:|---:|"]
    for r in lon_rows:
        lines.append(f"| {r.get('t (min)','')} | {r.get('RSS (MB)','')} | {r.get('FDs','')} | {r.get('MAVSDK','')} | {r.get('Storage (MB)','')} | {r.get('p95 latency (ms)','')} |")
    lines += ["", "### Result directories", "", f"- Comparative: `{comp}`", f"- Reliability: `{rel}`", f"- Longitudinal: `{lon}`", ""]
    (out / "LEADER_EVALUATION_TABLES.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(out / "source_result_sets.json", {"comparative":str(comp),"reliability":str(rel),"longitudinal":str(lon)})
    print(f"LEADER_TABLES={out / 'LEADER_EVALUATION_TABLES.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
