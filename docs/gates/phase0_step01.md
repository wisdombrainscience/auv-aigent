# STEP 01 — Freeze NAV-001

Status: PASS_WITH_ONE_DECLARED_EXTERNAL_POLICY_DEPENDENCY

Created:
- `specs/NAV-001.yaml`
- `specs/NAV-001.sha256`

Frozen in NAV-001:
- Module and capability identity
- User-provided inputs
- System-observed fields (anti-spoofing separation)
- Preconditions
- Output contract
- Failure codes
- Acceptance rules
- Evidence contract
- Immutability rules

Not invented from the master plan:
- Numeric permitted-test-region bounds.

Resolution:
- NAV-001 points to `config/experiment.yaml` for the permitted test region.
- Those numeric bounds must be explicitly set and frozen before SITL experiment execution.

Acceptance authority:
- Independent deterministic Python verifier.
- `module_output.status` and AI self-assessment cannot determine PASS.
