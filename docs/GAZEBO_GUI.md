# Gazebo desktop and simulator backend

V6.2.1 uses the official PX4 Gazebo Harmonic runtime image by default:

```text
px4io/px4-sitl-gazebo:v1.18.0-beta1-660-g61907962c9
```

The earlier lightweight `px4io/px4-sitl` image is SIH-only and must not be
paired with `PX4_SIM_MODEL=gz_x500`. `start-uav` now checks/pulls the matching
Gazebo image before the web server starts. Set `UAV_SIM_BACKEND=sih` only when
you deliberately want the lightweight headless SIH fallback.

# Gazebo Desktop Viewer

The flight runtime remains PX4 SITL / Gazebo simulation only. V6.2 can show the
same simulation in a native Gazebo desktop window while the localhost web UI is
open.

## Linux desktop

Start the console from a terminal inside the graphical desktop session:

```bash
./start-uav
```

Default mode is:

```bash
UAV_GAZEBO_GUI=auto
```

When a simulation is created, the runtime checks `DISPLAY`, `/tmp/.X11-unix`, and
`xauth`. It copies only the current display cookie into a private runtime file,
mounts the X11 socket and cookie read-only into the trusted simulator container,
and requests the Gazebo GUI client. This preserves X authentication; the project
does **not** use `xhost +` or `--privileged`.

If `xauth` is missing:

```bash
sudo apt install xauth
```

Both `/` and `/mission` expose **Open Gazebo**. The control is visualization-only
and has no authority over targets, telemetry evidence, or PASS/FAIL.

## Modes

```bash
# automatic desktop viewer when DISPLAY is usable
UAV_GAZEBO_GUI=auto ./start-uav

# explicitly disable the desktop viewer
UAV_GAZEBO_GUI=off ./start-uav

# explicitly request GUI mode (status reports an error if X11 is unavailable)
UAV_GAZEBO_GUI=on ./start-uav
```

For rendering compatibility, software rendering is enabled by default. Disable it
only when host/container graphics passthrough is known-good:

```bash
UAV_GAZEBO_SOFTWARE_RENDERING=0 ./start-uav
```

The pinned simulator image remains the project default so verified flight behavior
is not silently changed. An alternate trusted image can be selected explicitly:

```bash
UAV_SIM_IMAGE='<image>' ./start-uav
```

Changing the simulator image is a new runtime baseline and should be followed by
capability/mission acceptance tests.
