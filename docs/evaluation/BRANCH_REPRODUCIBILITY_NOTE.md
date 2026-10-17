# Evaluation v1.6.1 — Reproducibility Note

## Two regression counts must not be confused

### Frozen experimental working copy

The evaluation working copy used during the v1.6.1 freeze and experimental
development included the then-current local Phase 13 engineering state.

Recorded full regression:

    3028 passed

This value is retained as part of the historical frozen-baseline record.

### Isolated Git review branch

The reviewable evaluation branch is intentionally based on the committed
project baseline:

    1f55ba713826578bd51a3d7d474b85a6c3a64f55

Branch:

    evaluation/v1.6.1-verification

The uncommitted Phase 13 development workspace was deliberately not imported
into this branch.

Full regression executed directly against this isolated branch:

    2834 passed in 101.29 s

Targeted evaluation/simulation regression:

    21 passed in 1.63 s

Therefore, 3028 and 2834 refer to two different source scopes and must not be
presented as contradictory results.

## Evaluation integrity

The evaluation runtime, readiness logic, bounded simulator logging,
comparative/reliability/longitudinal harness, and associated evaluation tests
are committed on this review branch.

The original Phase 13 development working tree remains untouched.

## Official Comparative primary campaign

Requested cycles:

    30

Cycles reaching bounded PX4 flight readiness:

    25

Pre-execution infrastructure exclusions:

    5

Capability executions entering evaluation:

    100

Result:

    100 AGREE_PASS
    0 AGREE_FAIL
    0 SILENT_FAILURE
    0 VERIFIER_ONLY_LENIENT

The campaign must not be represented as "30/30 mission PASS".
