# Phase 1 — C1.1 Canonical IDs

Status: implementation candidate for the C1.1 gate.

Parent CP0 source identity:

- Version: `1.0.1`
- CP0 status: `GREEN`
- CP0 verifier: `PASS`
- Parent source archive: `remote_uav_cp0_source_20261003T024259Z.zip`
- Parent source archive SHA256: `499c3ef8dc90648356839408d16ac88491567118d352a22575eec66425b198e7`
- Parent source manifest SHA256: `effa1262fc0b5bfe496067a1f2f6c684e08b65090158fad3ec6dbe8fcac323fc`
- Frozen architecture baseline SHA256: `08028214edb8d4eb7791d994964415f3d6f5c4f3448f03414e5b7aee3a05e876`

## Scope

C1.1 defines identifiers for the new V8 engineering control plane only. Existing
UAV runtime identifiers such as `NAV-001`, `TAKEOFF-001`, `LAND-001`,
`RETURN-HOME-001`, and `MISSION-001` remain owned by the existing deterministic
UAV runtime and are intentionally not accepted as V8 control-plane IDs.

Canonical formats:

| Kind | Format | Example |
|---|---|---|
| project | `UAV-PROJ-NNNNNN` | `UAV-PROJ-000001` |
| requirement | `REQ-NNNNNN` | `REQ-000001` |
| task | `TASK-NNNNNN` | `TASK-000001` |
| artifact | `ART-NNNNNN` | `ART-000001` |
| agent run | `AI-RUN-NNNNNN` | `AI-RUN-000001` |
| review | `REVIEW-NNNNNN` | `REVIEW-000001` |
| validation | `VALID-NNNNNN` | `VALID-000001` |
| issue | `ISSUE-NNNN` | `ISSUE-0041` |

Sequence zero is reserved and rejected. Case, widths, prefixes, and whitespace
are strict. IDs are immutable and hashable.

`UAV-PROJ-000001` and `ISSUE-0041` are anchored to examples already present in
the frozen V8 architecture baseline. The other prefixes are new C1.1 contract
choices for the corresponding baseline entities.

## Allocation boundary

C1.1 does **not** allocate sequence numbers and does not introduce a global
counter. Persistent allocation belongs to the later Project State phase where
SQLite can provide transactional uniqueness. `CanonicalId.from_sequence()` only
formats a sequence supplied by its caller.

`ensure_unique_ids()` provides deterministic duplicate rejection for bounded
collections such as a proposed task plan. It is not a persistence allocator.

## Serialization

The stable C1.1 wire representation is exactly:

```json
{"kind": "task", "value": "TASK-000001"}
```

Unknown/missing fields and kind/value mismatches are rejected.

## Non-goals

C1.1 introduces no Ollama provider, model manager, Project State database,
artifact lifecycle, task lifecycle, orchestrator, retry loop, UAV Bridge, or PX4
behavior. It does not modify capability/verifier authority.
