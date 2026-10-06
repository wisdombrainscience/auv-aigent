# Gate — Phase 9 R9.2 Reviewer Role

R9.2 is GREEN only after target evidence confirms all tests and the standalone
acceptance probe pass.

Acceptance criteria:

1. The existing `reviewer` role from the four-role G6.1 MVP registry is reused.
2. No new role ID is introduced.
3. Reviewer capability is represented by an immutable profile.
4. The only structured output contract is C1.6/R9.1 `ReviewResult`.
5. A logical reviewer `AgentInstance` can be created without provider/model execution.
6. The profile has no issue allocation/persistence, artifact approval/rejection,
   review-task generation, routing, rework, duplicate suppression, task execution,
   or UAV authority.
7. The acceptance probe runs directly from project root without external
   `PYTHONPATH` setup.

Passing R9.2 does not make CP9 GREEN. R9.3–R9.7 remain pending.
