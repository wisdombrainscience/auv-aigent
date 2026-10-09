# F12.1 — Verifier Result Parser

## Purpose

Begin Phase 12 by converting one already-linked, independent mission verifier
output into a strict immutable control-plane contract without changing the
verifier's authority or inventing issue/rework semantics.

The parser consumes evidence only through the project-local U11.7 linkage. It
must not accept free-floating model text, UI state, or an unlinked run directory
as authoritative verifier input.

## Contract

```text
VERIFIER_RESULT_PARSER_CONTRACT_VERSION = 1.0
```

Public API:

```python
parsed = parse_linked_verifier_result(
    project_store,
    deployment_root,
    project_id,
    runtime_run_id,
)
```

`ParsedVerifierResult` preserves:

- project ID;
- exact requirement version;
- exact source artifact versions;
- runtime run ID;
- mission/scenario IDs;
- independent verifier `overall` (`PASS` or `FAIL`);
- all canonical mission verifier checks;
- deterministically derived failed check IDs;
- verifier-reported failed step IDs;
- transition/home metrics;
- verifier reason;
- exact `verification.json` SHA-256;
- `VERIFIER_RESULT` provenance.

## Evidence boundary

Before parsing, F12.1 revalidates that:

1. the U11.7 link exists and is structurally valid;
2. the linked attempt remains below the existing `runs/` root;
3. no attempt/evidence path component is a symbolic link;
4. `finalized.json` still matches the U11.7 `finalized_sha256` anchor;
5. the entire finalized attempt still passes evidence integrity checking;
6. `verification.json` matches the U11.7 `verification_sha256` anchor;
7. the finalized manifest itself binds that exact verifier hash.

Therefore Phase 12 cannot silently parse a verifier file that changed after the
UAV run was linked into Project State lineage.

## Frozen MISSION-001 verifier schema

The parser requires exactly these verifier fields:

```text
overall
checks
max_transition_distance_m
terminal_home_distance_m
failed_step_ids
reason
```

The canonical check set is:

```text
step_sequence
child_verdicts
child_evidence_integrity
continuity
terminal_land_step
terminal_landed_state
terminal_home
```

Each check and `overall` is exactly `PASS` or `FAIL`. `overall` must be `PASS`
if and only if every canonical check is `PASS`.

F12.1 may derive `failed_checks` from those deterministic check verdicts. That is
normalization, not simulation-issue classification.

## Authority and provenance

F12.1 does not create a second verdict. `overall` is copied from the independent
mission verifier only after evidence/hash/schema validation.

The parsed contract is explicitly:

```text
provenance = VERIFIER_RESULT
```

The existing distinction remains:

```text
SIMULATION_RESULT != VERIFIER_RESULT
```

## Reserved for later Phase-12 gates

F12.1 deliberately does not implement:

```text
F12.2 simulation issue taxonomy
F12.3 issue -> engineering role mapping
F12.4 lineage invalidation
F12.5 rework task generation
F12.6 bounded simulation retry
F12.7 NEEDS_HUMAN transition
```

It adds no provider/model call, PX4/MAVSDK access, runtime invocation, retry,
issue persistence, or task-state mutation.

## Checkpoint status

Phase 11 / CP9 remain GREEN. Phase 12 remains in progress and CP10 remains
PENDING after F12.1; CP10 may become GREEN only after the complete bounded
simulation-feedback loop is stable.
