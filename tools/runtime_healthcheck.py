#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from src.runtime_paths import runs_root


def ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"PASS  {label}{suffix}")


def warn(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"WARN  {label}{suffix}")


def fail(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"FAIL  {label}{suffix}")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check local UAV runtime prerequisites")
    ap.add_argument("--project", type=Path, default=PROJECT)
    ap.add_argument("--require-gazebo-gui", action="store_true")
    args = ap.parse_args()
    project = args.project.expanduser().resolve()
    failures = 0

    print(f"PROJECT {project}")
    data_runs = runs_root(project)
    print(f"RUNS_ROOT {data_runs}")
    try:
        data_runs.mkdir(parents=True, exist_ok=True)
        probe = data_runs / ".health-write-test"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        ok("Runtime data directory", str(data_runs))
    except OSError as exc:
        fail("Runtime data directory", str(exc))
        failures += 1
    try:
        from importlib.metadata import version

        try:
            pkg_version = version("uav-capability-framework")
        except Exception:
            pkg_version = "source-tree"
        ok("Python", sys.version.split()[0])
        print(f"PACKAGE_VERSION {pkg_version}")
    except Exception as exc:
        fail("Python runtime", str(exc))
        failures += 1

    try:
        import yaml  # noqa: F401
        import mavsdk  # noqa: F401
        ok("Python dependencies", "yaml + mavsdk")
    except Exception as exc:
        fail("Python dependencies", f"{exc}; run ./setup-uav")
        failures += 1

    docker = shutil.which("docker")
    if not docker:
        fail("Docker", "docker command not found")
        failures += 1
    else:
        info = run([docker, "info"])
        if info.returncode == 0:
            ok("Docker daemon")
        else:
            fail("Docker daemon", (info.stderr or info.stdout).strip()[:300])
            failures += 1

    try:
        from orchestration.step37a_simulation import SimulationSupervisor

        supervisor = SimulationSupervisor(project)
        cfg = supervisor.config
        ok("Simulation backend", f"{cfg.backend} / {cfg.model}")
        print(f"SIM_IMAGE {cfg.image}")
        if docker:
            inspect = run([docker, "image", "inspect", cfg.image])
            if inspect.returncode == 0:
                ok("Simulator image", "installed")
            else:
                warn("Simulator image", "not installed; ./start-uav will pull it")

        if cfg.backend == "gazebo" and cfg.gazebo_gui != "off":
            display = os.environ.get("DISPLAY")
            if not display:
                message = "DISPLAY is not set"
                if args.require_gazebo_gui:
                    fail("Gazebo desktop", message)
                    failures += 1
                else:
                    warn("Gazebo desktop", message + "; headless fallback is available")
            elif not shutil.which("xauth"):
                message = "xauth is missing"
                if args.require_gazebo_gui:
                    fail("Gazebo desktop", message)
                    failures += 1
                else:
                    warn("Gazebo desktop", message)
            else:
                auth = run(["xauth", "nlist", display])
                if auth.returncode == 0 and auth.stdout.strip():
                    ok("Gazebo desktop", f"authenticated DISPLAY={display}")
                else:
                    message = f"no Xauthority cookie for DISPLAY={display}"
                    if args.require_gazebo_gui:
                        fail("Gazebo desktop", message)
                        failures += 1
                    else:
                        warn("Gazebo desktop", message)
    except Exception as exc:
        fail("Simulation configuration", str(exc))
        failures += 1

    stale = []
    for pattern in ("*.rej", "*.orig"):
        stale.extend(project.rglob(pattern))
    if stale:
        warn("Patch residue", ", ".join(str(p.relative_to(project)) for p in stale[:8]))
    else:
        ok("Patch residue", "none")

    print(f"HEALTHCHECK={'PASS' if failures == 0 else 'FAIL'} failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
