# Developer Guide

## Setup

```bash
./uavctl install --dev
./uavctl test
```

## Cấu trúc chính

```text
src/core/                 generic capability abstractions
src/capabilities/         capability implementations/catalog
src/adapters/             PX4/MAVSDK boundary
src/verification/         deterministic verifiers
src/evidence/             evidence/finalization/index
src/resources/            resource policy and guards
src/mission/              mission model
orchestration/            session/capability/mission orchestration
user_interface/           localhost web UI
specs/                    frozen capability/mission specs
config/                   runtime/input/resource policy
tests/unit/               regression tests
tools/                    console, packaging, maintenance
```

## Non-negotiable design rules

1. AI/UI/module không quyết định official PASS/FAIL.
2. Không sửa frozen acceptance/verifier chỉ để làm implementation PASS.
3. Không sửa finalized evidence.
4. PX4-specific logic nằm sau adapter boundary.
5. Telemetry phải freeze trước verification/post-action.
6. Consumer live data phải bounded; raw evidence có retention/quota policy riêng.

## Thêm capability mới

Quy trình khuyến nghị:

1. Viết/freeze spec `specs/<CAPABILITY>.yaml`.
2. Implement module dưới `src/capabilities/`.
3. Viết verifier độc lập trong `src/verification/`.
4. Register qua capability catalog.
5. Thêm evidence contract.
6. Unit test module/verifier/registry/executor.
7. SITL acceptance test.
8. Repeatability/soak.
9. Chỉ sau đó compose vào Mission.

Không tạo một runner riêng nếu generic executor đã đáp ứng được.

## Test

```bash
./uavctl test
```

Static sanity:

```bash
python3 -m compileall -q src orchestration user_interface tools
bash -n uavctl start-uav test-uav setup-uav health-uav storage-uav
```

## Git workflow

- `main`: stable/releasable.
- feature branch: `feature/...`, `fix/...`, `docs/...`.
- PR phải chạy CI xanh.
- Không commit `runs`, `.venv`, `uav.env`, secrets hoặc local ZIP/patch.

## Build release

```bash
./uavctl github-check
./uavctl test
./uavctl package
```

Artifact trong `dist/` có manifest SHA256 riêng.
