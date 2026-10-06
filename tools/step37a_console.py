#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestration.web_experiment import ExperimentStore, Step37AExperimentManager
from orchestration.web_mission import MissionWebManager
from orchestration.step37a_simulation import SimulationSupervisor

PAGE = (Path(__file__).resolve().parents[1] / "user_interface" / "step37a_console.html").read_text(encoding="utf-8")
MISSION_PAGE = (Path(__file__).resolve().parents[1] / "user_interface" / "mission_builder.html").read_text(encoding="utf-8")


class ConsoleState:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.store = ExperimentStore()
        self.simulation = SimulationSupervisor(project)
        self.manager = Step37AExperimentManager(project, self.store, simulation=self.simulation)
        self.missions = MissionWebManager(project, simulation=self.simulation)
        self.execution_lock = threading.Lock()


def handler_factory(state: ConsoleState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            accept_encoding = self.headers.get("Accept-Encoding", "")
            gzip_min = state.manager.resource_policy.json_gzip_min_bytes
            use_gzip = "gzip" in accept_encoding.lower() and len(body) >= gzip_min
            if use_gzip:
                body = gzip.compress(body, compresslevel=5)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Vary', 'Accept-Encoding')
            if use_gzip:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            n = int(self.headers.get('Content-Length', '0'))
            if n <= 0 or n > 32768:
                raise ValueError('invalid request size')
            return json.loads(self.rfile.read(n).decode())

        def do_GET(self):
            parsed = urlparse(self.path)
            p = parsed.path
            if p == '/':
                body = PAGE.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            if p == '/mission':
                body = MISSION_PAGE.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            if p == '/api/simulation':
                return self.json(state.simulation.live_status())
            if p == '/api/session':
                return self.json(state.manager.session_status())
            if p == '/api/config':
                return self.json(state.manager.public_config())
            if p == '/api/resources':
                return self.json(state.manager.resource_status())
            if p == '/api/history':
                q = parse_qs(parsed.query)
                limit = int((q.get('limit') or ['50'])[0])
                offset = int((q.get('offset') or ['0'])[0])
                return self.json(state.manager.history(limit=limit, offset=offset))
            if p.startswith('/api/mission/'):
                parts = p.strip('/').split('/')
                if len(parts) == 4 and parts[3] == 'status':
                    q = parse_qs(parsed.query)
                    compact = (q.get('compact') or ['0'])[0].lower() in {'1','true','yes'}
                    item = state.missions.get(parts[2], include_trajectory=not compact)
                    if not item: return self.json({'error':'mission run not found'},404)
                    return self.json(item)
                if len(parts) == 4 and parts[3] == 'telemetry':
                    q = parse_qs(parsed.query)
                    since_raw = (q.get('since') or [None])[0]
                    limit_raw = (q.get('limit') or [None])[0]
                    since = float(since_raw) if since_raw not in {None, ''} else None
                    limit = int(limit_raw) if limit_raw not in {None, ''} else None
                    rows = state.missions.latest_trajectory_for_run(
                        parts[2], since_timestamp=since, limit=limit
                    )
                    return self.json({'run_id':parts[2],'rows':rows})
            if p.startswith('/api/experiment/'):
                parts = p.strip('/').split('/')
                if len(parts) == 4:
                    rid, kind = parts[2], parts[3]
                    item = state.store.get(rid)
                    if not item: return self.json({'error':'run not found'},404)
                    if kind == 'status': return self.json(item)
                    if kind == 'telemetry':
                        q = parse_qs(parsed.query)
                        since_raw = (q.get('since') or [None])[0]
                        limit_raw = (q.get('limit') or [None])[0]
                        since = float(since_raw) if since_raw not in {None, ''} else None
                        limit = int(limit_raw) if limit_raw not in {None, ''} else None
                        return self.json({
                            'run_id':rid,
                            'rows':state.manager.latest_telemetry_for_run(
                                rid, since_timestamp=since, limit=limit
                            ),
                        })
            return self.json({'error':'not found'},404)

        def do_POST(self):
            p = urlparse(self.path).path
            try:
                raw = self.read_json()
                if p == '/api/session/reset':
                    if state.missions.has_active_run():
                        raise RuntimeError('cannot reset single-flight session while a mission is running')
                    return self.json(state.manager.reset_session(raw.get('start_position_m')))
                if p == '/api/session/continue':
                    if state.missions.has_active_run():
                        raise RuntimeError('cannot continue single-flight session while a mission is running')
                    return self.json(state.manager.continue_session(raw.get('target_position_m')))
                if p == '/api/session/land':
                    if state.missions.has_active_run():
                        raise RuntimeError('cannot use session Land while a mission is running')
                    return self.json(state.manager.land_session())
                if p == '/api/experiment/start':
                    with state.execution_lock:
                        if state.missions.has_active_run():
                            raise RuntimeError('cannot start a single flight while a mission is running')
                        session = state.manager.session.snapshot()
                        server_start = session.get('current_requested_start_position_m')
                        if not isinstance(server_start, list) or len(server_start) != 3:
                            raise RuntimeError('active session has no valid start position; restart simulation')
                        owned = dict(raw)
                        owned['start_position_m'] = list(server_start)
                        canonical = state.manager.validate_request(owned)
                        rid = state.manager.start(canonical)
                    return self.json({'run_id':rid,'status':'QUEUED','scope':'SIMULATION_ONLY'})
                if p == '/api/simulation/gazebo':
                    if state.manager.has_active_run() or state.missions.has_active_run():
                        # Opening a viewer is visualization-only and safe while a run
                        # executes, but avoid racing simulator creation/removal.
                        if not state.simulation.is_running():
                            raise RuntimeError('simulation is still starting')
                    return self.json(state.simulation.launch_gazebo_gui())
                if p == '/api/mission/start':
                    with state.execution_lock:
                        if state.manager.has_active_run():
                            raise RuntimeError('cannot start a mission while a single flight is running')
                        # Mission owns/reset the simulator, so any legacy interactive
                        # session must be treated as stale before mission execution.
                        state.manager.session.mark_reset_required()
                        rid = state.missions.start(raw)
                    return self.json({'run_id':rid,'status':'QUEUED','scope':'SIMULATION_ONLY'})
                return self.json({'error':'not found'},404)
            except Exception as exc:
                return self.json({'error':str(exc)},400)

        def log_message(self, fmt, *args): print(fmt % args)
    return Handler


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--project',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=8370); ap.add_argument('--self-test',action='store_true'); args=ap.parse_args()
    project=args.project.expanduser().resolve()
    if args.self_test:
        sample={'start_position_m':[0,0,-2],'target_position_m':[1,1,-2],'position_tolerance_m':.5,'start_position_tolerance_m':.75,'timeout_s':5,'frame':'LOCAL_NED','scenario_id':'SELFTEST'}
        Step37AExperimentManager.validate_request(sample); print('STEP37A_SELF_TEST=PASS'); return 0
    if not project.is_dir(): raise SystemExit(f'project not found: {project}')
    if args.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('STEP37A console is localhost-only; use --host 127.0.0.1')
    state=ConsoleState(project); server=ThreadingHTTPServer((args.host,args.port),handler_factory(state)); print('=== NAV-001 STEP 37A CONSOLE ==='); print('SCOPE=SIMULATION_ONLY'); print('VEHICLE_CONTROL=PX4_SITL_ONLY'); print('VERDICT_AUTHORITY=INDEPENDENT_PYTHON_VERIFIER'); print(f'URL=http://{args.host}:{args.port}'); print('SESSION=RESET_OR_CONTINUE'); print('COORDINATE_FRAME=LOCAL_NED');
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); state.simulation.stop()
    return 0

if __name__=='__main__': raise SystemExit(main())
