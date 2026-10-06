# R9.3 — Review Task Generation

R9.3 generates one bounded persisted review task for one immutable artifact
version whose producer task explicitly declared `review_required=true`.

The task reuses the frozen Phase-5 Task Engine. It is created with role
`reviewer`, starts in `CREATED` with `attempt=0`, depends on the source
artifact's producer task, and references the exact `ART-######@vN` version as
its sole input artifact. The generated review task sets
`review_required=false` so review generation does not recurse.

R9.3 uses `review_result.v1` as task output-schema metadata because the frozen
R9.2 reviewer capability outputs `ReviewResult`. R9.3 does **not** extend G6.5
provider-side schema enforcement or execute the reviewer; those behaviors are
outside this gate.

Generation is idempotent for the same artifact version across restart. A
bounded project-local coordination lock serializes generation, while the
Phase-5 task registry remains source of truth; no new SQLite table is added.

A new task may be generated only while the target artifact version is `DRAFT`.
If an R9.3 task already exists, reopening it remains idempotent even after the
artifact moves to a later lifecycle state.

Out of scope: artifact lifecycle transition, issue allocation/persistence,
issue-to-owner routing (R9.4), rework task creation (R9.5), max-rework gating
(R9.6), duplicate issue suppression (R9.7), provider/model execution, task
execution, and UAV authority.
