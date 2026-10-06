# NAV-001 STEP 36A — Local Model Benchmark

This bundle benchmarks the five **unique installed model images/configurations**
reported by the workstation.

It follows the frozen NAV-001 architecture:
- same frozen task;
- same prompt;
- same protected APIs;
- same deterministic candidate validator;
- same targeted/full tests;
- accepted `go_to_target.py` is NOT shown to the models.

Phase 36A intentionally does not start PX4/Gazebo/SITL. Heavy local inference
finishes before any later simulation, matching the project's resource policy.

Run preparation first, then run **one model at a time**.

After a model creates a candidate:
1. targeted validation;
2. full validation only if targeted validation passes.

Finally aggregate Phase A. Phase 36B should handle recovery and SITL verification
for the benchmark candidates without changing acceptance criteria.

The preparation step deliberately reuses the already-proven text-only
`generator` agent profile from
`~/UAV/st0_agent_jobs/NAV-001/recovery_002/workspace/.opencode/`.
This keeps the benchmark runner/profile constant while only the local model
changes. Preparation fails rather than silently switching to a different agent
profile.
