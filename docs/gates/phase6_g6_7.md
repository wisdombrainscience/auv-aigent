# Gate G6.7 — Context Manifest

Parent: G6.6 GREEN.

Acceptance requires:
1. exact-parent installer preflight PASS;
2. full target regression PASS;
3. `python3 tools/check_context_manifest.py` prints `CONTEXT_MANIFEST=PASS`;
4. restart verification PASS;
5. context and manifest hashes verify;
6. run registry remains exactly 2 tables;
7. task state remains unchanged;
8. provider calls remain none;
9. artifact creation remains reserved for G6.8.
