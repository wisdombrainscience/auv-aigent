# V7 Deployment Edition

Version: **1.0.0**

V7 is a packaging/resource release based on the functionally stable V6.2.6
runtime. No flight capability, mission step, Gazebo view, or verifier authority
is removed.

## Added

- `uavctl` unified install/start/doctor/test/storage/package command;
- production `install-uav` without developer dependencies by default;
- persistent `UAV_DATA_DIR` support;
- safe legacy data migration;
- explicit storage retention dry-run/apply tooling;
- clean reproducible source release builder and SHA256 manifest.

## Optimized

- recent run cache: 256 -> 64;
- live UI trajectory buffer: 600 -> 300 points;
- incremental Single Flight and Mission telemetry transfer;
- compact Mission status responses;
- gzip JSON and HTTP/1.1 keep-alive;
- resource disk-size scan caching;
- incremental SQLite history backfill;
- no pip download cache during installation.

Raw telemetry evidence remains at 10 Hz and deterministic verification logic is
unchanged.
