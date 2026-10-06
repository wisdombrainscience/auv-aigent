# C1.9 — Phase 1 Contract Integration and Invalid-State Tests

Status: candidate for CP1 gate.

## Purpose

C1.9 does not introduce another runtime contract. It closes Phase 1 by proving
that the contracts added in C1.1–C1.8 remain mutually compatible, serialize as
strict JSON, reject authority bypasses, and preserve the frozen boundaries.

The integration suite covers:

- all Phase 1 contract versions and package exports;
- canonical IDs;
- task-state invalid shortcuts and crash recovery;
- artifact-state invalid shortcuts;
- provenance strictness;
- AgentResult proposal-only authority;
- ReviewResult advisory-only authority;
- ValidationResult child-check consistency;
- Failure Taxonomy separation and no retry authorization;
- representative cross-contract JSON round trips;
- simulation vs verification failure separation;
- no imports from the UAV execution plane;
- no retry/sleep loop primitives inside the contract layer.

## Important boundary

C1.9 is test/documentation work only. It does not implement:

- LocalAIProvider/OllamaProvider/MockProvider;
- persistence or Project State;
- task scheduling or retries;
- Agent Engine;
- reviewer/validator execution;
- orchestrator;
- RAG;
- UAV Bridge;
- any PX4/Gazebo/capability/mission behavior.

## CP1 close condition

CP1 may be marked GREEN only after this overlay is applied to the real target
tree and the entire project regression suite passes with zero failures.

After CP1 GREEN, the next authorized phase is Phase 2 — Local Provider.
