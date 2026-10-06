# Gate R9.1 — Review / Feedback Issue Schema

Parent: **O8.8 GREEN / CP8 GREEN**  
Frozen architecture: `RUAV-AI-ARCH-V8.0-BL1`

## Gate requirements

R9.1 is GREEN only when all of the following hold on the target deployment:

1. complete project regression passes;
2. the C1.6 `ReviewIssue` wire schema is reused unchanged as the standalone
   Phase-9 review/feedback issue schema;
3. `REVIEW_ISSUE_CONTRACT_VERSION` and `parse_review_issue()` are exposed;
4. issue IDs accept only canonical `ISSUE-####` identifiers;
5. fingerprint is mandatory but no new hashing/generation policy is invented;
6. V7.10 `ValidationIssue` remains a distinct deterministic contract;
7. later owner-routing/rework/lifecycle fields are rejected;
8. no issue allocation/persistence, reviewer execution, provider call or UAV
   runtime interaction is introduced.

Acceptance command:

```bash
python3 tools/check_phase9_review_issue_schema.py
```

Expected marker:

```text
REVIEW_ISSUE_SCHEMA=PASS
```

Passing R9.1 does not make CP9 GREEN. R9.2–R9.7 remain pending.
