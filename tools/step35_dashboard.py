#!/usr/bin/env python3
"""
NAV-001 STEP 35 local dashboard + runtime-input form.

Safety / architecture constraints:
- Binds to localhost by default.
- Does NOT start PX4, Gazebo, MAVSDK, Docker, or any vehicle process.
- Does NOT change verifier results or accepted-candidate status.
- Does NOT allow user-supplied system-observed state.
- Stages user runtime input artifacts only.
- The existing experiment runner / canonical validator remains authoritative.

Standard-library only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
FORBIDDEN_SYSTEM_FIELDS = {
    "current_position",
    "vehicle_state",
    "airborne_state",
    "localization_valid",
    "connection_state",
    "armed",
    "airborne",
    "connected",
}
ALLOWED_FRAME = "LOCAL_NED"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def finite_number(value, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field}: boolean is not a number")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{field}: NaN/Infinity not allowed")
    return out


def validate_user_request(doc: dict) -> tuple[bool, dict, list[str]]:
    errors: list[str] = []

    if not isinstance(doc, dict):
        return False, {}, ["request must be a JSON object"]

    forbidden_present = sorted(FORBIDDEN_SYSTEM_FIELDS.intersection(doc))
    if forbidden_present:
        errors.append(
            "system-observed fields are not user-settable: "
            + ", ".join(forbidden_present)
        )

    frame = doc.get("frame", ALLOWED_FRAME)
    if frame != ALLOWED_FRAME:
        errors.append(
            f"unsupported frame for frozen NAV-001 UI: {frame!r}; "
            f"expected {ALLOWED_FRAME}"
        )

    target = doc.get("target_position_m")
    canonical_target = None
    if not isinstance(target, list) or len(target) != 3:
        errors.append("target_position_m must be a 3-element array")
    else:
        try:
            canonical_target = [
                finite_number(target[0], "target_x"),
                finite_number(target[1], "target_y"),
                finite_number(target[2], "target_z"),
            ]
        except ValueError as exc:
            errors.append(str(exc))

    tolerance = None
    try:
        tolerance = finite_number(
            doc.get("position_tolerance_m"),
            "position_tolerance_m",
        )
        if tolerance <= 0:
            errors.append("position_tolerance_m must be > 0")
    except ValueError as exc:
        errors.append(str(exc))

    timeout_s = None
    try:
        timeout_s = finite_number(doc.get("timeout_s"), "timeout_s")
        if timeout_s <= 0:
            errors.append("timeout_s must be > 0")
    except ValueError as exc:
        errors.append(str(exc))

    scenario_id = str(doc.get("scenario_id") or "").strip()
    if not scenario_id:
        scenario_id = "UI-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if len(scenario_id) > 80:
        errors.append("scenario_id must be <= 80 characters")

    if errors:
        return False, {}, errors

    canonical = {
        "task_id": "NAV-001",
        "frame": ALLOWED_FRAME,
        "target_position_m": canonical_target,
        "position_tolerance_m": tolerance,
        "timeout_s": timeout_s,
        "scenario_id": scenario_id,
    }
    return True, canonical, []


class DashboardState:
    def __init__(self, project: Path):
        self.project = project
        self.status_path = project / "reports/pipeline_v2/step19_30_status.json"
        self.matrix_summary_path = (
            project / "reports/pipeline_v2/step34_matrix/matrix_summary.json"
        )
        self.matrix_results_path = (
            project / "reports/pipeline_v2/step34_matrix/matrix_results.csv"
        )
        self.ui_root = project / "reports/pipeline_v2/ui_inputs"
        self.status = {}
        self.matrix = {}
        self.candidate_hash = ""
        self.accepted_candidate = None
        self.refresh()

    def refresh(self):
        if not self.status_path.is_file():
            raise RuntimeError(f"missing accepted status: {self.status_path}")
        if not self.matrix_summary_path.is_file():
            raise RuntimeError(
                f"missing STEP34 matrix summary: {self.matrix_summary_path}"
            )

        self.status = load_json(self.status_path)
        self.matrix = load_json(self.matrix_summary_path)

        if self.status.get("candidate_status") != "ACCEPTED":
            raise RuntimeError("candidate_status is not ACCEPTED")
        if self.status.get("scope") != "SIMULATION_ONLY":
            raise RuntimeError("accepted scope is not SIMULATION_ONLY")
        if self.matrix.get("overall") != "PASS":
            raise RuntimeError("STEP34 matrix overall is not PASS")

        self.candidate_hash = self.status["candidate_sha256"]
        self.accepted_candidate = (
            self.project
            / "reports/pipeline_v2/accepted/NAV-001"
            / self.candidate_hash
            / "go_to_target.py"
        )
        if not self.accepted_candidate.is_file():
            raise RuntimeError("accepted candidate file is missing")
        if sha256(self.accepted_candidate) != self.candidate_hash:
            raise RuntimeError("accepted candidate SHA256 mismatch")

    def stage(self, raw_doc: dict) -> dict:
        ok, canonical, errors = validate_user_request(raw_doc)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        artifact_dir = self.ui_root / stamp
        artifact_dir.mkdir(parents=True, exist_ok=False)

        raw_path = artifact_dir / "raw_user_input.json"
        validation_path = artifact_dir / "input_validation.json"
        input_path = artifact_dir / "input.json"

        raw_path.write_text(
            json.dumps(raw_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        validation_doc = {
            "valid": ok,
            "errors": errors,
            "authority": (
                "UI pre-validation only; experiment runner/canonical validator "
                "remains authoritative"
            ),
            "system_state_user_spoofing_allowed": False,
        }
        validation_path.write_text(
            json.dumps(validation_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        if ok:
            input_path.write_text(
                json.dumps(canonical, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        return {
            "valid": ok,
            "errors": errors,
            "canonical_input": canonical if ok else None,
            "artifact_dir": str(artifact_dir),
            "input_json": str(input_path) if ok else None,
            "note": (
                "This dashboard stages input only. It does not start a simulation "
                "and does not modify the verifier verdict."
            ),
        }


def page_html(state: DashboardState) -> str:
    m = state.matrix
    candidate_short = html.escape(state.candidate_hash[:16] + "…")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NAV-001 STEP 35 Dashboard</title>
<style>
:root {{
  font-family: system-ui, -apple-system, Segoe UI, sans-serif;
  color: #20242a; background:#f5f7f9;
}}
body {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
h1 {{ margin-bottom: 4px; }}
.banner {{ background:#fff3cd; padding:12px 16px; border-radius:10px; margin:14px 0; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; }}
.card {{ background:white; border:1px solid #dde3e8; border-radius:12px; padding:16px; }}
.value {{ font-size:1.55rem; font-weight:700; margin-top:6px; }}
.pass {{ color:#147a3d; }}
form {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
label {{ display:flex; flex-direction:column; gap:5px; font-size:.92rem; }}
input {{ padding:10px; border:1px solid #aeb8c2; border-radius:7px; }}
button {{ grid-column:1/-1; padding:11px; border:0; border-radius:8px; cursor:pointer; }}
pre {{ white-space:pre-wrap; background:#111827; color:#e5e7eb; padding:14px; border-radius:8px; }}
small {{ color:#5b6670; }}
@media(max-width:650px) {{ form {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<h1>NAV-001 GoToTarget</h1>
<div>STEP 35 local dashboard / runtime-input form</div>
<div class="banner">
<strong>PX4 SITL simulation project only.</strong>
This page cannot start PX4, arm a vehicle, or alter verifier results.
</div>

<div class="grid">
<div class="card"><small>Candidate</small><div class="value">{candidate_short}</div></div>
<div class="card"><small>Acceptance</small><div class="value pass">ACCEPTED</div></div>
<div class="card"><small>Matrix</small><div class="value pass">{m.get("pass_count")}/{m.get("scenario_count")} PASS</div></div>
<div class="card"><small>Success rate</small><div class="value">{m.get("success_rate_percent"):.1f}%</div></div>
<div class="card"><small>Mean final error</small><div class="value">{m.get("mean_final_error_m"):.3f} m</div></div>
<div class="card"><small>Max final error</small><div class="value">{m.get("max_final_error_m"):.3f} m</div></div>
</div>

<h2>Stage a runtime input</h2>
<p>
Only user-provided runtime fields are accepted. Connection, airborne,
localization, vehicle state and current position remain system-observed.
</p>

<div class="card">
<form id="inputForm">
<label>Target X (m)<input id="x" type="number" step="any" value="10" required></label>
<label>Target Y (m)<input id="y" type="number" step="any" value="5" required></label>
<label>Target Z / Down (m)<input id="z" type="number" step="any" value="-3" required></label>
<label>Tolerance (m)<input id="tol" type="number" step="any" value="0.5" min="0.000001" required></label>
<label>Timeout (s)<input id="timeout" type="number" step="any" value="30" min="0.000001" required></label>
<label>Scenario ID<input id="scenario" value="STEP35-UI-DEMO" maxlength="80"></label>
<label>Frame<input value="LOCAL_NED" disabled></label>
<button type="submit">Validate and stage input.json</button>
</form>
</div>

<h2>Result</h2>
<pre id="result">No UI input staged yet.</pre>

<script>
const form = document.getElementById("inputForm");
const result = document.getElementById("result");
form.addEventListener("submit", async (ev) => {{
  ev.preventDefault();
  const payload = {{
    frame: "LOCAL_NED",
    target_position_m: [
      Number(document.getElementById("x").value),
      Number(document.getElementById("y").value),
      Number(document.getElementById("z").value)
    ],
    position_tolerance_m: Number(document.getElementById("tol").value),
    timeout_s: Number(document.getElementById("timeout").value),
    scenario_id: document.getElementById("scenario").value
  }};
  result.textContent = "Validating…";
  try {{
    const r = await fetch("/api/stage-input", {{
      method: "POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify(payload)
    }});
    const body = await r.json();
    result.textContent = JSON.stringify(body, null, 2);
  }} catch (e) {{
    result.textContent = String(e);
  }}
}});
</script>
</body>
</html>"""


def make_handler(state: DashboardState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NAV001Step35/1.0"

        def _json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                try:
                    state.refresh()
                    body = page_html(state).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if parsed.path == "/api/status":
                try:
                    state.refresh()
                    self._json({
                        "task": "NAV-001",
                        "scope": "SIMULATION_ONLY",
                        "candidate_status": state.status.get("candidate_status"),
                        "candidate_sha256": state.candidate_hash,
                        "matrix": state.matrix,
                        "dashboard_can_execute_vehicle_actions": False,
                        "dashboard_can_modify_verdict": False,
                    })
                except Exception as exc:
                    self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/api/stage-input":
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 32_768:
                    raise ValueError("invalid request size")
                raw = self.rfile.read(length)
                doc = json.loads(raw.decode("utf-8"))
                result = state.stage(doc)
                status = HTTPStatus.OK if result["valid"] else HTTPStatus.BAD_REQUEST
                self._json(result, status)
            except Exception as exc:
                self._json(
                    {"valid": False, "errors": [str(exc)]},
                    HTTPStatus.BAD_REQUEST,
                )

        def log_message(self, fmt, *args):
            print(
                datetime.now(timezone.utc).isoformat(),
                self.address_string(),
                fmt % args,
            )

    return Handler


def run_self_test(project: Path) -> int:
    state = DashboardState(project)
    tests = []

    def check(name, doc, expected):
        ok, _canonical, _errors = validate_user_request(doc)
        passed = ok is expected
        tests.append({"name": name, "pass": passed})
        if not passed:
            raise RuntimeError(f"self-test failed: {name}")

    valid = {
        "frame": "LOCAL_NED",
        "target_position_m": [10, 5, -3],
        "position_tolerance_m": 0.5,
        "timeout_s": 30,
        "scenario_id": "STEP35-SELFTEST",
    }
    check("valid_input", valid, True)

    bad = dict(valid)
    bad["position_tolerance_m"] = -1
    check("negative_tolerance_rejected", bad, False)

    bad = dict(valid)
    bad["timeout_s"] = 0
    check("nonpositive_timeout_rejected", bad, False)

    bad = dict(valid)
    bad["frame"] = "GLOBAL"
    check("unsupported_frame_rejected", bad, False)

    bad = dict(valid)
    bad["target_position_m"] = [float("nan"), 0, -3]
    check("nan_rejected", bad, False)

    bad = dict(valid)
    bad["airborne"] = True
    check("system_state_spoof_rejected", bad, False)

    with tempfile.TemporaryDirectory() as td:
        test_state = DashboardState(project)
        test_state.ui_root = Path(td) / "ui_inputs"
        staged = test_state.stage(valid)
        if not staged["valid"]:
            raise RuntimeError("staging valid input failed")
        if not Path(staged["input_json"]).is_file():
            raise RuntimeError("staged input.json missing")
        tests.append({"name": "artifact_staging", "pass": True})

    status_doc = {
        "task": "NAV-001",
        "step": 35,
        "dashboard": "PASS",
        "scope": "SIMULATION_ONLY",
        "localhost_only_default": True,
        "vehicle_control_enabled": False,
        "verdict_mutation_enabled": False,
        "system_state_spoofing": "REJECTED",
        "candidate_sha256": state.candidate_hash,
        "step34_matrix": "PASS",
        "self_tests": tests,
    }
    out = project / "reports/pipeline_v2/step35_dashboard_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(status_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("=== NAV-001 STEP 35 SELF-TEST ===")
    for t in tests:
        print(f"{t['name']}=PASS")
    print("STEP34_MATRIX=PASS")
    print("ACCEPTED_CANDIDATE_HASH=PASS")
    print("SYSTEM_STATE_ANTI_SPOOFING=PASS")
    print("DASHBOARD_VEHICLE_CONTROL=DISABLED")
    print("DASHBOARD_VERDICT_MUTATION=DISABLED")
    print("STEP35_DASHBOARD=PASS")
    print(f"STATUS={out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    project = args.project.expanduser().resolve()

    if args.self_test:
        return run_self_test(project)

    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit(
            "REFUSED: STEP35 dashboard must bind to localhost only "
            "(127.0.0.1 or localhost)"
        )

    state = DashboardState(project)
    handler = make_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler)

    print("=== NAV-001 STEP 35 DASHBOARD ===")
    print("SCOPE=SIMULATION_ONLY")
    print("VEHICLE_CONTROL=DISABLED")
    print("VERDICT_MUTATION=DISABLED")
    print(f"URL=http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop the dashboard.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
