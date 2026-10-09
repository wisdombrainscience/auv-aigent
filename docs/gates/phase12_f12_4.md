# F12.4 — Lineage Invalidation Gate

Status: candidate until target evidence is complete.

Acceptance requires:

- PASS feedback performs no artifact lifecycle mutation.
- FAIL feedback invalidates every exact source artifact version used by the simulation.
- existing A4.6 STALE propagation marks transitive descendants STALE.
- unrelated artifacts remain unchanged.
- all roots are prevalidated before lifecycle mutation.
- repeated invalidation is state-idempotent.
- requirement version, issue fingerprints, verifier hash, and VERIFIER_RESULT provenance are preserved.
- no ISSUE ID allocation, rework task, simulation retry, NEEDS_HUMAN transition, provider call, or PX4 access occurs.
