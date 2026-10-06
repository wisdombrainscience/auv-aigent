# V4.4 Resource Cleanup Hardening

V4.4 addresses a soak-test failure observed after 27 consecutive NAV-001 runs.
The failure attempt was created and then failed before the telemetry recorder
started. The error handler then attempted to finalize the frozen evidence
contract without `telemetry.csv`, masking the original runtime error. At the
same time, process file descriptors and threads spiked, consistent with delayed
cleanup of per-run MAVSDK resources.

Changes:

- create a schema-only `telemetry.csv` before PX4 connection/setup so early
  failures remain finalizable without fabricated samples;
- preserve the original runtime exception separately from verifier/evidence
  errors;
- force `RESET_REQUIRED` in the last-resort worker error path;
- explicitly stop the embedded MAVSDK server via the pinned MAVSDK System
  cleanup hook instead of waiting for Python garbage collection;
- do adapter cleanup before a run is exposed as terminal to the UI/soak tool;
- report child process and `mavsdk_server` process counts in `/api/resources`;
- make the soak gate require zero embedded `mavsdk_server` child processes after
  each terminal run.

The deterministic NAV-001 verdict and frozen execution-window evidence remain
unchanged. V4.4 only hardens lifecycle/resource cleanup and failure reporting.
