# V7.2 — Deterministic Finite-Number Checks

## Scope

V7.2 is the second work package in frozen Phase 7 — Deterministic Engineering Trust.
It executes the task-declared `finite_values` validator against one persisted artifact
and returns the existing C1.7 `ValidationResult` contract.

The validator ID remains `finite_values` because that identifier is already present in
the frozen Phase-5 task contract/tests. The roadmap label “finite-number checks” does
not create a second validator namespace.

V7.2 does **not** persist validation results and does **not** change artifact or task
lifecycle state. The frozen artifact transition order remains:

```text
DRAFT → REVIEWED → VALIDATED → APPROVED
```

## Deterministic check

The validator first opens the persisted artifact through the Phase-4 registry and
content-addressed store, so the recorded SHA-256 remains authoritative for blob
integrity. It then decodes bounded JSON with duplicate-key detection and recursively
checks every numeric leaf.

Rejected inputs include:

- `NaN`, `Infinity`, and `-Infinity` constants;
- floating-point overflow such as `1e309` / `-1e309` that decodes non-finite;
- integers too large to be represented as a finite ordinary Python/binary64 float;
- invalid UTF-8 or invalid JSON;
- duplicate object keys.

Booleans are not numbers. Arrays and objects are traversed deterministically.

The finite-number validator is deliberately artifact-type agnostic. Schema validity is
owned by V7.1; V7.2 only answers whether the JSON numeric leaves are finite and usable
by later deterministic engineering calculators.

## Task binding

The producer `TaskRecord` remains source of truth. V7.2 runs only when the task
contains:

```text
validators: [..., finite_values, ...]
```

A missing/mismatched producer task, missing artifact, or content-hash corruption is an
operational conflict and fails closed rather than being disguised as an engineering
FAIL.

## Result

One C1.7 check is returned:

```text
check_id = finite_values
status   = PASS | FAIL
```

PASS evidence includes the bounded count of numeric leaves inspected. FAIL includes a
stable field path where possible, for example:

```text
$.calculations[0].mass.kg
```

## Non-goals

V7.2 does not implement:

- V7.3 unit validation;
- mass/energy/reserve/thrust/mission calculators;
- V7.9 constraint framework;
- V7.10 validation issue schema;
- DRAFT → REVIEWED;
- REVIEWED → VALIDATED;
- validation-result persistence;
- artifact approval;
- provider/model calls;
- PX4/MAVSDK/Gazebo control.
