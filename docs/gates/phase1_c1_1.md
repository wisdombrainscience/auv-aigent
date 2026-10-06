# PHASE 1 / C1.1 Gate — Canonical IDs

Parent: CP0 `20261003T024259Z`, source archive SHA256
`499c3ef8dc90648356839408d16ac88491567118d352a22575eec66425b198e7`.

Required checks:

- canonical ID implementation exists under `ai/contracts/`;
- empty/whitespace/case/width/zero/overflow IDs are rejected;
- serialization round-trip passes;
- malformed serialization is rejected;
- duplicate IDs are rejected deterministically;
- existing UAV runtime IDs are not reinterpreted;
- full pre-existing regression remains green;
- no UAV Core, PX4 adapter, mission, verifier, or capability implementation is changed.

C1.1 must be GREEN before C1.2 Task States begins.
