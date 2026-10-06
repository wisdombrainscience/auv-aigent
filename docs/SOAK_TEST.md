# NAV-001 Soak / Resource Stability Test

Run the web console in terminal 1:

```bash
./start-uav
```

Then run a short validation in terminal 2:

```bash
./soak-uav --reset --flights 10
```

For the stabilization gate:

```bash
./soak-uav --reset --flights 50
```

The runner uses the same localhost HTTP API as the browser. It repeatedly flies a
small deterministic 3D loop inside the canonical test region, waits for the
independent verifier, then records server RSS, file-descriptor count, thread
count, evidence size and verdict.

Reports are written under `reports/soak/`.

A run is `PASS` when all requested flights pass, the resource guard remains OK,
and process RSS growth after the warm-up flights stays within the configured
warning threshold (64 MB by default). This is an engineering stability gate, not
a flight-safety certification.

During the soak, also watch the `start-uav` terminal for MAVSDK messages such as
`User callback queue slow`. Repeatedly increasing queue sizes are a reason to
stop and investigate even if the HTTP-level soak report passes.
