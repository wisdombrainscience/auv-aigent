# Build Validation — v1.0.1 GitHub-ready Deployment Baseline

Validated: 2026-09-30.

## Automated validation

```text
194 passed
Python compileall PASS
Shell entrypoint syntax PASS
GitHub preflight PASS
Release-builder manifest integrity PASS
```

The GitHub-ready packaging work changes documentation, CI/repository hygiene,
preflight tooling, and release-manifest generation. Flight/capability/mission
acceptance behavior is unchanged from the validated V7 deployment baseline.

## Target-host acceptance inherited from the deployment baseline

Previously validated on the target Linux desktop:

- Gazebo GUI + PX4 SITL startup;
- continuous Single Flight from ground through multiple targets;
- explicit Land and re-takeoff flow;
- TAKEOFF-001 / NAV-001 / RETURN-HOME-001 / LAND-001;
- multi-waypoint Mission Builder;
- deterministic capability and mission verification;
- bounded MAVSDK/resource behavior and storage controls.

## Before a public GitHub push

```bash
./uavctl github-check
./uavctl test
```

## Before creating a release

```bash
./uavctl package
```

Review the generated ZIP and SHA256 asset, then tag the exact commit that was
used to build the release.
