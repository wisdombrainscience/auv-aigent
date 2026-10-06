# Gate — Phase 3 S3.1 SQLite Project Schema

S3.1 is GREEN only when CP2 is GREEN, installer preflight/apply pass, full target regression passes, the schema probe passes after close/reopen, schema version mismatch and foreign/partial databases fail closed, canonical ID sequence rows are exact, invalid project IDs are rejected by SQLite, foreign keys are enabled, and no S3.2-S3.9 behavior is implemented early.
