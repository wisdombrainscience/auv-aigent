# Gate V7.7 — Thrust-to-Weight

Status: candidate pending target validation.

## Acceptance

V7.7 is accepted only when:

- exact-parent V7.6 preflight passes;
- deterministic thrust-to-weight unit tests pass;
- full regression passes;
- `tools/check_phase7_thrust_to_weight.py` prints `THRUST_TO_WEIGHT=PASS`;
- valid ratio reconciliation PASSes and a mismatched ratio FAILs;
- restart produces the same result;
- artifact remains DRAFT, persisted validation remains none, task state is unchanged;
- provider calls remain none.

Reserved: V7.8 mission distance and every later Phase-7 gate.
