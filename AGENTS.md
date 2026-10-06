# UAV Capability Framework — Project Instructions

This repository implements the NAV-001 GoToTarget proof of concept.

## Non-negotiable rules

- The frozen specification is `specs/NAV-001.yaml`.
- Acceptance criteria, acceptance tests, and deterministic verification are not editable by implementation or recovery agents.
- AI must never decide official PASS/FAIL.
- Official PASS/FAIL comes only from deterministic Python verification using experiment evidence.
- Do not edit previous run evidence.
- Keep PX4-specific logic behind the UAVAdapter boundary.
- Prefer the smallest patch that satisfies the frozen specification.
- Every agent job must report changed files, commands used, and a short summary.

## Protected areas

Implementation and recovery jobs must not edit:

- `specs/**`
- `tests/acceptance/**`
- `src/verification/**`
- `runs/**`

Test-generation jobs may only write candidate tests under:

- `tests/unit/generated/**`

AI-generated tests are not acceptance tests until separately reviewed and frozen.
