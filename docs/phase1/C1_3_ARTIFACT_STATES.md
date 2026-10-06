# C1.3 — Artifact States

Status: candidate for target gate.

## Scope

C1.3 defines the persisted state vocabulary and deterministic direct-transition
contract for one immutable engineering artifact version.

Canonical states are exactly:

`DRAFT -> REVIEWED -> VALIDATED -> APPROVED`

with the additional states:

`REJECTED`, `STALE`, `SUPERSEDED`.

## Version semantics

A rejected, stale, or superseded artifact version does not return to DRAFT.
Rework produces a new artifact version later. C1.3 does not create that new
version; version creation and lineage are Phase 4 responsibilities.

A dependency change may make an otherwise active artifact version `STALE`.
A newer artifact version may make an active version `SUPERSEDED`.

`APPROVED` is state-level integration eligibility only. C1.3 deliberately does
not claim that an approved artifact is current: currentness requires lineage,
dependency freshness, and project-state checks that are deferred to Phase 4.

## Deliberate boundaries

C1.3 does not implement:

- artifact persistence or a registry;
- artifact IDs beyond the already-frozen C1.1 contract;
- lineage/dependency graphs;
- provenance classes;
- reviewer/validator execution;
- project integration or source merging;
- Ollama, agents, orchestration, RAG, or UAV/PX4 behavior.

## Safety/trust boundary

No existing UAV Runtime Core file is modified. Artifact state never authorizes
direct PX4/MAVSDK/actuator access and never decides a simulation verdict.
