# CP3 — Project Persistence Stable

CP3 is GREEN only after S3.1 through S3.10 are GREEN on the target.

Required final target evidence:

```text
./uavctl test
python3 tools/check_project_recovery.py
```

The recovery acceptance must prove restart readability for requirements, facts,
assumptions, and decisions, plus rollback of an interrupted S3.9 transaction.

CP3 does not authorize Phase 4 early. Phase 4 begins only after these checks are
reported GREEN from the target.
