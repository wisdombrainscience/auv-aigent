from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.runtime_paths import runtime_root


@dataclass(frozen=True)
class SimulationConfig:
    backend: str = "gazebo"  # gazebo | sih
    image: str = "px4io/px4-sitl-gazebo:v1.18.0-beta1-660-g61907962c9"
    model: str = "gz_x500"
    world: str = "default"
    container_name: str = "nav001-step37a-sim"
    require_sudo_n: bool = False
    gazebo_gui: str = "auto"  # auto | on | off
    software_rendering: bool = True


class SimulationSupervisor:
    """Own one PX4 SITL/Gazebo simulation for the localhost console.

    The flight runtime and the visualizer deliberately remain separate concerns:
    PX4/Gazebo may continue headless if desktop X11 forwarding is unavailable,
    while the browser still receives deterministic telemetry/verdicts. On a
    local Linux graphical session, ``gazebo_gui=auto`` securely forwards the
    current X11/XWayland display into the trusted PX4 container and requests a
    Gazebo GUI client without disabling X authentication.
    """

    def __init__(self, project: Path, config: SimulationConfig | None = None) -> None:
        self.project = Path(project).resolve()
        if config is None:
            backend = str(os.environ.get("UAV_SIM_BACKEND", "gazebo")).strip().lower()
            if backend not in {"gazebo", "sih"}:
                raise ValueError("UAV_SIM_BACKEND must be gazebo or sih")
            gui_mode = str(os.environ.get("UAV_GAZEBO_GUI", "auto")).strip().lower()
            if gui_mode in {"1", "true", "yes"}:
                gui_mode = "on"
            elif gui_mode in {"0", "false", "no"}:
                gui_mode = "off"
            if gui_mode not in {"auto", "on", "off"}:
                gui_mode = "auto"
            if backend == "gazebo":
                default_image = "px4io/px4-sitl-gazebo:v1.18.0-beta1-660-g61907962c9"
                default_model = "gz_x500"
            else:
                default_image = "px4io/px4-sitl@sha256:c0fd482ece15bc2eecf0108ae70f8b3fa026faa4c167119f3641702b6fb20d09"
                default_model = "sihsim_quadx"
                gui_mode = "off"
            image = str(os.environ.get("UAV_SIM_IMAGE") or default_image)
            config = SimulationConfig(
                backend=backend,
                image=image,
                model=str(os.environ.get("UAV_SIM_MODEL") or default_model),
                world=str(os.environ.get("UAV_GZ_WORLD") or "default"),
                container_name=str(
                    os.environ.get("UAV_SIM_CONTAINER") or "nav001-step37a-sim"
                ),
                require_sudo_n=False,
                gazebo_gui=gui_mode,
                software_rendering=str(
                    os.environ.get("UAV_GAZEBO_SOFTWARE_RENDERING", "1")
                ).lower()
                not in {"0", "false", "no"},
            )
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._xauth_path: Path | None = None
        self._gui_launch_requested = False
        self._gui_error: str | None = None
        self._gui_display: str | None = None
        self._log_handle: Any | None = None
        self._simulator_log_path = runtime_root(self.project) / "simulator.log"
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        backend = self.config.backend
        if backend not in {"gazebo", "sih"}:
            raise ValueError("simulation backend must be gazebo or sih")
        if backend == "gazebo":
            if not self.config.model.startswith("gz_"):
                raise ValueError("Gazebo backend requires a PX4_SIM_MODEL beginning with gz_")
            official_sih = (
                self.config.image.startswith("px4io/px4-sitl:")
                or self.config.image.startswith("px4io/px4-sitl@")
            )
            if official_sih:
                raise ValueError(
                    "Gazebo backend cannot use the lightweight px4io/px4-sitl SIH image; "
                    "use px4io/px4-sitl-gazebo or set UAV_SIM_BACKEND=sih"
                )
        elif self.config.model.startswith("gz_"):
            raise ValueError("SIH backend cannot use a gz_* model")

    def _read_simulator_log_tail(self, max_lines: int = 60) -> str:
        try:
            if self._log_handle is not None:
                self._log_handle.flush()
        except Exception:
            pass
        try:
            lines = self._simulator_log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-max_lines:])

    def _container_run_command(
        self, pose: str, gui_args: list[str], gui_setup: dict[str, Any]
    ) -> list[str]:
        args: list[str] = [
            "run",
            "--rm",
            "--name",
            self.config.container_name,
            "--network",
            "host",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            *gui_args,
            "-e",
            f"PX4_SIM_MODEL={self.config.model}",
        ]
        if self.config.backend == "gazebo":
            args += [
                "-e",
                f"PX4_GZ_WORLD={self.config.world}",
                "-e",
                f"PX4_GZ_MODEL_POSE={pose}",
            ]
            # The Gazebo runtime image must be told to stay headless when no
            # authenticated desktop display is available. In GUI mode we leave
            # HEADLESS unset, as required by the PX4 container launcher.
            if not gui_setup.get("available"):
                args += ["-e", "HEADLESS=1"]
        args.append(self.config.image)
        return self._docker(*args)

    @staticmethod
    def ned_to_gazebo_pose(position_ned_m: list[float]) -> str:
        """Map local NED (North, East, Down) to Gazebo ENU pose (East, North, Up)."""
        x_n, y_e, z_d = [float(v) for v in position_ned_m]
        return f"{y_e:.6f},{x_n:.6f},{-z_d:.6f},0,0,0"

    def _docker(self, *args: str) -> list[str]:
        prefix = ["sudo", "-n", "docker"] if self.config.require_sudo_n else ["docker"]
        return prefix + list(args)

    def is_running(self) -> bool:
        result = subprocess.run(
            self._docker("inspect", "-f", "{{.State.Running}}", self.config.container_name),
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _remove_xauth(self) -> None:
        if self._xauth_path is not None:
            try:
                self._xauth_path.unlink(missing_ok=True)
            except OSError:
                pass
        self._xauth_path = None

    def _prepare_x11(self) -> tuple[list[str], dict[str, Any]]:
        mode = self.config.gazebo_gui
        if self.config.backend != "gazebo":
            return [], {
                "mode": "off",
                "display": os.environ.get("DISPLAY"),
                "available": False,
                "requested": False,
                "status": "DISABLED",
                "error": "Gazebo GUI is disabled because UAV_SIM_BACKEND=sih",
            }
        display = os.environ.get("DISPLAY")
        status: dict[str, Any] = {
            "mode": mode,
            "display": display,
            "available": False,
            "requested": False,
            "status": "DISABLED" if mode == "off" else "UNAVAILABLE",
            "error": None,
        }
        if mode == "off":
            return [], status
        if not display:
            status["error"] = "DISPLAY is not set; start-uav from the local graphical desktop session"
            return [], status
        if not Path("/tmp/.X11-unix").is_dir():
            status["error"] = "/tmp/.X11-unix is unavailable on the host"
            return [], status
        xauth = shutil.which("xauth")
        if not xauth:
            status["error"] = "xauth is not installed (Ubuntu: sudo apt install xauth)"
            return [], status

        listed = subprocess.run(
            [xauth, "nlist", display],
            check=False,
            capture_output=True,
            text=True,
        )
        if listed.returncode != 0 or not listed.stdout.strip():
            status["error"] = (
                f"no Xauthority cookie found for DISPLAY={display}; "
                "check XAUTHORITY/desktop session"
            )
            return [], status

        runtime_dir = runtime_root(self.project)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        auth_path = runtime_dir / "gazebo.xauth"
        auth_path.unlink(missing_ok=True)
        # Docker container hostname differs from the host. Wildcard the address
        # family exactly as recommended by PX4's authenticated X11 recipe.
        cookie_lines = []
        for line in listed.stdout.splitlines():
            if line.strip():
                cookie_lines.append("ffff" + line[4:] if len(line) >= 4 else line)
        merged = subprocess.run(
            [xauth, "-f", str(auth_path), "nmerge", "-"],
            input="\n".join(cookie_lines) + "\n",
            check=False,
            capture_output=True,
            text=True,
        )
        if merged.returncode != 0 or not auth_path.is_file() or auth_path.stat().st_size == 0:
            auth_path.unlink(missing_ok=True)
            status["error"] = "failed to create private Xauthority file for Gazebo"
            return [], status
        auth_path.chmod(0o600)
        self._xauth_path = auth_path
        self._gui_display = display
        status.update({"available": True, "status": "READY", "error": None})
        args = [
            "--mount",
            "type=bind,src=/tmp/.X11-unix,dst=/tmp/.X11-unix,readonly",
            "--mount",
            f"type=bind,src={auth_path},dst=/tmp/uav-gazebo.xauth,readonly",
            "-e",
            f"DISPLAY={display}",
            "-e",
            "XAUTHORITY=/tmp/uav-gazebo.xauth",
            "-e",
            "QT_QPA_PLATFORM=xcb",
            "-e",
            "QT_X11_NO_MITSHM=1",
        ]
        if self.config.software_rendering:
            args += ["-e", "LIBGL_ALWAYS_SOFTWARE=1"]
        return args, status

    def _gazebo_processes(self) -> str:
        if not self.is_running():
            return ""
        result = subprocess.run(
            self._docker(
                "exec",
                self.config.container_name,
                "sh",
                "-lc",
                "ps -eo args | grep -E '[g]z sim|[r]uby .*gz sim' || true",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def gazebo_gui_status(self) -> dict[str, Any]:
        enabled = self.config.gazebo_gui != "off"
        processes = self._gazebo_processes() if self.is_running() else ""
        gui_detected = False
        for line in processes.splitlines():
            lowered = line.lower()
            if "gz sim" not in lowered:
                continue
            if " -g" in lowered or "--gui-only" in lowered:
                gui_detected = True
                break
            # A gz-sim invocation with neither server-only flag nor headless
            # setting normally owns both server and GUI.
            if " -s" not in lowered and "--server-only" not in lowered and "headless" not in lowered:
                gui_detected = True
                break
        if not enabled:
            status = "DISABLED"
        elif self._gui_error:
            status = "ERROR"
        elif gui_detected or self._gui_launch_requested:
            status = "RUNNING" if self.is_running() else "STOPPED"
        elif self._gui_display:
            status = "READY"
        else:
            status = "UNAVAILABLE"
        return {
            "mode": self.config.gazebo_gui,
            "display": self._gui_display or os.environ.get("DISPLAY"),
            "requested": self._gui_launch_requested,
            "status": status,
            "error": self._gui_error,
            "processes": processes,
        }

    def launch_gazebo_gui(self) -> dict[str, Any]:
        """Request a Gazebo client in the active simulator container.

        This is a visualization action only. It never affects verifier authority
        or flight targets. The container must have been started with authenticated
        X11 forwarding, which ``start_new`` prepares in auto/on GUI mode.
        """
        if not self.is_running():
            raise RuntimeError("simulation container is not running")
        if self.config.gazebo_gui == "off":
            raise RuntimeError("Gazebo GUI is disabled by UAV_GAZEBO_GUI=off")
        if not self._gui_display or self._xauth_path is None:
            raise RuntimeError(
                self._gui_error
                or "Gazebo GUI is unavailable; restart simulation from a graphical terminal"
            )

        processes = self._gazebo_processes()
        for line in processes.splitlines():
            lowered = line.lower()
            if "gz sim" in lowered and (
                " -g" in lowered
                or "--gui-only" in lowered
                or (" -s" not in lowered and "--server-only" not in lowered and "headless" not in lowered)
            ):
                self._gui_launch_requested = True
                self._gui_error = None
                return self.gazebo_gui_status()

        result = subprocess.run(
            self._docker(
                "exec",
                "-d",
                self.config.container_name,
                "sh",
                "-lc",
                "gz sim -g -v 2 >/tmp/uav-gazebo-gui.log 2>&1",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self._gui_error = (
                result.stderr.strip()
                or result.stdout.strip()
                or "docker exec failed while launching Gazebo GUI"
            )
            raise RuntimeError(self._gui_error)
        self._gui_launch_requested = True
        self._gui_error = None
        time.sleep(0.15)
        return self.gazebo_gui_status()

    def live_status(self) -> dict[str, object]:
        return {
            "managed": True,
            "backend": self.config.backend,
            "image": self.config.image,
            "container_name": self.config.container_name,
            "model": self.config.model,
            "world": self.config.world,
            "status": "RUNNING" if self.is_running() else "STOPPED",
            "gazebo_gui": self.gazebo_gui_status(),
        }

    def stop(self) -> None:
        cmd = self._docker("rm", "-f", self.config.container_name)
        subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        self._gui_launch_requested = False
        self._gui_error = None
        self._gui_display = None
        self._remove_xauth()

    def start_new(self, start_position_ned_m: list[float]) -> dict[str, object]:
        self.stop()
        pose = self.ned_to_gazebo_pose(start_position_ned_m)
        gui_args, gui_setup = self._prepare_x11()
        self._gui_error = gui_setup.get("error") if gui_setup.get("error") else None
        if (
            self.config.backend == "gazebo"
            and self.config.gazebo_gui == "on"
            and not gui_setup.get("available")
        ):
            raise RuntimeError(
                self._gui_error
                or "Gazebo GUI was required but desktop display forwarding is unavailable"
            )

        cmd = self._container_run_command(pose, gui_args, gui_setup)
        self._simulator_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._simulator_log_path.open("w", encoding="utf-8")
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=self.project,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
            raise RuntimeError("docker/sudo is unavailable") from exc

        deadline = time.monotonic() + 45.0
        stable_since: float | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                code = self._process.returncode
                tail = self._read_simulator_log_tail()
                detail = ("\n--- simulator.log (tail) ---\n" + tail) if tail else ""
                raise RuntimeError(
                    "PX4 simulation container exited during startup "
                    f"(backend={self.config.backend}, image={self.config.image}, "
                    f"model={self.config.model}, exit={code})." + detail
                )

            if self.is_running():
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 2.0:
                    # In auto/on mode the container was created with an
                    # authenticated X11 cookie. If the image did not launch a GUI
                    # itself, request one explicitly. This is visualization-only.
                    if gui_setup.get("available"):
                        try:
                            self.launch_gazebo_gui()
                        except Exception as exc:
                            self._gui_error = str(exc)
                            if self.config.gazebo_gui == "on":
                                self.stop()
                                raise RuntimeError(
                                    "Gazebo GUI was required but could not be started: "
                                    + str(exc)
                                ) from exc
                    return {
                        "managed": True,
                        "backend": self.config.backend,
                        "image": self.config.image,
                        "container_name": self.config.container_name,
                        "spawn_position_ned_m": list(start_position_ned_m),
                        "gazebo_pose": pose,
                        "model": self.config.model,
                        "world": self.config.world,
                        "status": "RUNNING",
                        "gazebo_gui": self.gazebo_gui_status(),
                    }
            else:
                stable_since = None
            time.sleep(0.25)

        tail = self._read_simulator_log_tail()
        detail = ("\n--- simulator.log (tail) ---\n" + tail) if tail else ""
        self.stop()
        raise RuntimeError(
            "PX4 simulation container did not remain running for the startup readiness window"
            + detail
        )
