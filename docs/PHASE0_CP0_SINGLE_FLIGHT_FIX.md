# CP0 Single Flight acceptance correction

The original CP0 harness called raw `capability-uav NAV-001` from a physical ground spawn. That is not a valid Single Flight acceptance path because `NAV-001` correctly requires `airborne=true`.

The corrected harness uses the production localhost Single Flight flow:

1. start Remote UAV headless on an isolated local port;
2. reset the server-owned session at `[0, 0, 0]`;
3. start a Single Flight to `[5, 0, -3]`;
4. require `auto_takeoff_bootstrap=true`;
5. require the independent verifier to report PASS for position, timeout, and telemetry;
6. require no adapter cleanup error and no active MAVSDK server after the run;
7. stop the server and require the owned simulator container and MAVSDK server processes to be cleaned up.

This change modifies only the CP0 acceptance harness. It does not change NAV-001, TAKEOFF-001, PX4 behavior, verifier authority, or the frozen V8 architecture baseline.


## Fix2: deployment port precedence

Observed on deployment v1.0.1:

- CP0 helper requested `uavctl start --headless --port <random>`.
- The deployment wrapper sourced `uav.env` and started the console on `UAV_PORT=8370`.
- The helper continued polling the random port and timed out even though the real server was healthy.

Fix2 does **not** modify the UAV runtime or `uavctl`.  The CP0 helper now reads the deployment's configured `UAV_PORT` (default 8370), verifies that port is free, starts the product with `uavctl start --headless`, and probes the actual configured port.

This keeps Phase 0 focused on baseline verification.  The CLI-vs-environment precedence behavior remains a separately documented deployment issue and can be corrected under a later controlled maintenance work package.
