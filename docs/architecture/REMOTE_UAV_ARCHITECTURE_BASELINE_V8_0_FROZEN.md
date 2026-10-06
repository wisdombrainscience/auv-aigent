# REMOTE UAV — FROZEN ARCHITECTURE & EXECUTION BASELINE

**Baseline ID:** `RUAV-AI-ARCH-V8.0-BL1`  
**Status:** `FROZEN`  
**Date:** `2026-10-03`  
**Purpose:** Canonical design baseline for the next implementation stages of Remote UAV.  
**Primary rule:** No implementation may silently change this baseline. Any architecture change must be proposed as an explicit Change Request (CR), reviewed, accepted, versioned, and recorded.

---

## 0. HOW THIS BASELINE MUST BE USED

This file is the source of truth for the next development stages.

Before implementing any new AI/agent feature:

1. Read this baseline first.
2. Identify the Phase and Work Package being implemented.
3. Check its dependencies and acceptance gate.
4. Do not implement work from later phases early unless an explicit Change Request approves it.
5. Do not modify the stable UAV Runtime Core merely to make the AI layer easier to implement.
6. If implementation behavior conflicts with this baseline, stop and resolve the conflict before continuing.
7. If a task becomes larger than expected, split it before continuing.
8. If repeated errors exceed the defined retry limit, transition to `NEEDS_HUMAN`; never create an unbounded retry loop.

**Conflict precedence:**

```text
Explicit newer user-approved Change Request
                >
This frozen baseline
                >
Implementation convenience / model suggestion
```

---

# 1. PRODUCT GOAL

Remote UAV is a **local-first UAV engineering platform**.

The target user experience is:

```text
User requirement
      ↓
Local AI engineering team
      ↓
Structured engineering project
      ↓
Deterministic engineering validation
      ↓
Existing Remote UAV capability / mission runtime
      ↓
PX4 SITL + Gazebo
      ↓
Evidence
      ↓
Independent deterministic verification
      ↓
Feedback / targeted rework
      ↓
Simulation-verified UAV project
```

The product is **not** a chatbot that directly controls a UAV.

The product is an engineering system in which local AI helps to:

- understand requirements;
- identify missing information;
- decompose engineering work;
- create structured design proposals;
- perform role-specific engineering reasoning;
- write candidate code in a sandbox;
- review artifacts;
- interpret deterministic validator results;
- create simulation proposals;
- analyze verifier failures;
- perform bounded, targeted rework.

The AI is not the final authority for engineering calculations or flight/simulation verdicts.

---

# 2. FROZEN SYSTEM INVARIANTS

These rules are architecture invariants unless explicitly changed by a future approved CR.

## 2.1 UAV Runtime invariants

```text
Execution scope        = SIMULATION ONLY
Vehicle control        = PX4 SITL
Primary simulation     = Gazebo
Internal frame         = LOCAL_NED
Airborne NED z         = negative
Operator altitude      = positive-up
Verdict authority      = independent deterministic verifier
```

Current capability family:

```text
TAKEOFF-001
NAV-001
RETURN-HOME-001
LAND-001
MISSION-001
```

`MISSION-001` composes capability executions but has its own independent mission-level verification.

The verifier, not the UI and not an AI model, owns authoritative simulation PASS/FAIL.

## 2.2 AI invariants

```text
Cloud LLM              = DISABLED
Default AI mode        = LOCAL ONLY
Primary provider       = Ollama
Test provider          = MockProvider
Direct AI → PX4        = FORBIDDEN
Direct AI → actuator   = FORBIDDEN
Unbounded retry        = FORBIDDEN
Agent output as truth  = FORBIDDEN
```

AI may create proposals.  
AI may not self-approve its own proposal as final project truth.

## 2.3 Project invariants

```text
Conversation != source of truth
Project State  = source of truth

Agent result   = DRAFT until validated
Approved data  = versioned
Dependencies   = tracked
Stale outputs  = invalidated
```

---

# 3. TOP-LEVEL ARCHITECTURE

The platform is organized into five logical planes.

```text
┌───────────────────────────────────────────────────────────┐
│ USER PLANE                                                │
│ requirements / files / decisions / monitoring             │
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────┐
│ ORCHESTRATION PLANE                                       │
│ Requirement Engine                                        │
│ Orchestrator                                               │
│ Plan Validator                                             │
│ Task DAG                                                   │
│ Scheduler                                                  │
│ Agent Engine                                               │
│ Feedback Router                                            │
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────┐
│ PROJECT DATA PLANE                                        │
│ Project State                                              │
│ Facts / Assumptions / Decisions                            │
│ Artifacts                                                  │
│ Versions / Lineage                                         │
│ Task state                                                 │
│ Local documents                                            │
│ AI run evidence                                            │
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────┐
│ TRUST PLANE                                               │
│ Schemas                                                    │
│ Unit checks                                                │
│ Engineering calculators                                    │
│ Constraints                                                │
│ AI Reviewer                                                │
│ Tool permissions                                           │
│ Human approval gates                                       │
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────┐
│ UAV EXECUTION PLANE                                       │
│ Capability Framework                                       │
│ Mission Runtime                                            │
│ MAVSDK / PX4 SITL                                          │
│ Gazebo                                                     │
│ Telemetry                                                  │
│ Evidence                                                   │
│ Independent Verifier                                       │
└───────────────────────────────────────────────────────────┘
```

Each plane must have a distinct responsibility. No module should simultaneously create, approve, execute, and verify the same artifact.

---

# 4. CANONICAL END-TO-END PIPELINE

```text
USER
 │
 ▼
01 PROJECT INTAKE
 │
 ▼
02 INPUT NORMALIZATION / UNTRUSTED-DATA HANDLING
 │
 ▼
03 REQUIREMENT ENGINE
 │
 ├── critical information missing ─────► USER
 │
 ▼
04 VERSIONED REQUIREMENT SET
 │
 ▼
05 PROJECT STATE
 │
 ▼
06 ORCHESTRATOR
 │
 ▼
07 TASK PLAN PROPOSAL
 │
 ▼
08 DETERMINISTIC PLAN VALIDATOR
 │
 ├── FAIL ─────► PLAN REWORK
 │
 ▼
09 APPROVED TASK DAG
 │
 ▼
10 RESOURCE-AWARE SCHEDULER
 │
 ▼
11 CONTEXT BUILDER
 │
 ▼
12 LOCAL AGENT INSTANCE
 │
 ▼
13 ALLOWLISTED TOOLS / SANDBOX
 │
 ▼
14 DRAFT ARTIFACT
 │
 ▼
15 AI CROSS-REVIEW
 │
 ├── REWORK ───► TARGETED TASK REWORK
 │
 ▼
16 DETERMINISTIC ENGINEERING VALIDATION
 │
 ├── FAIL ─────► TARGETED TASK REWORK
 │
 ▼
17 APPROVED VERSIONED ARTIFACT
 │
 ▼
18 ARTIFACT REGISTRY + LINEAGE
 │
 ▼
19 PROJECT INTEGRATOR
 │
 ▼
20 APPROVED PROJECT STATE
 │
 ▼
21 UAV BRIDGE
 │
 ▼
22 EXISTING CAPABILITY / MISSION VALIDATION
 │
 ▼
23 PX4 SITL / GAZEBO EXECUTION
 │
 ▼
24 UAV EVIDENCE
 │
 ▼
25 INDEPENDENT UAV VERIFIER
 │
 ├── FAIL ─────► SIMULATION ISSUE CLASSIFICATION
 │                  │
 │                  ▼
 │             FEEDBACK ROUTER
 │                  │
 │                  ▼
 │             TARGETED REWORK
 │
 ▼
26 COMPLETION ASSESSMENT
 │
 ▼
FINAL PROJECT STATUS
```

---

# 5. AUTHORITY MODEL

There are three different validation authorities.

| Layer | Responsibility | Authority |
|---|---|---|
| AI Reviewer | semantic critique, contradictions, unsupported claims | advisory |
| Deterministic Engineering Validator | schemas, units, calculations, constraints | authoritative for checks it implements |
| UAV Independent Verifier | simulation/capability/mission PASS/FAIL | sole authoritative simulation verdict |

A PASS from the AI Reviewer can never replace deterministic validation.

A statement from an AI Agent can never replace `verification.json` for simulation result authority.

---

# 6. LOCAL AI PROVIDER ARCHITECTURE

Only local model runtimes are in baseline scope.

```text
Agent Engine
     │
     ▼
Model Manager
     │
     ▼
LocalAIProvider
     │
     ├── OllamaProvider
     └── MockProvider
```

Future local providers may be added only behind `LocalAIProvider`, for example:

```text
LlamaCppProvider
VLLMProvider
```

They are not required for V8 foundation.

No OpenAI/Anthropic/Gemini cloud provider belongs in the baseline implementation.

## 6.1 Provider responsibility

Provider:

```text
health()
list_models()
generate()
```

Provider must not own:

- project state;
- task state;
- scheduling;
- agent roles;
- artifact approval;
- retry loops beyond a small provider-level transient retry policy.

## 6.2 Model Manager responsibility

Model Manager owns:

- selected local model profile;
- provider health;
- inference queue;
- model keep-alive policy;
- basic resource awareness;
- model load/unload policy when supported.

---

# 7. MULTI-AGENT MODEL

Multiple agents do not imply multiple model processes.

Default architecture:

```text
                    ONE LOCAL MODEL
                           │
          ┌────────────────┼────────────────┐
          │                │                │
      Agent A          Agent B          Agent C
      Role A           Role B           Role C
      Task A           Task B           Task C
      Context A        Context B        Context C
      Memory A         Memory B         Memory C
      Tools A          Tools B          Tools C
```

Agents are logical, isolated role/task instances.

The default deployment must assume only one local model is loaded.

---

# 8. ROLE REGISTRY

Initial role registry may contain:

```text
coordinator
requirements_engineer
uav_systems_engineer
propulsion_power_engineer
airframe_engineer
px4_flight_engineer
software_engineer
simulation_engineer
verification_engineer
reviewer
```

Not all roles are spawned for every project.

A Dynamic Team Builder selects only roles required by the validated task plan.

Initial MVP should use only:

```text
coordinator
requirements_engineer
uav_systems_engineer
reviewer
```

Specialized roles are added only after the core workflow is stable.

---

# 9. REQUIREMENT MODEL

Requirements are versioned, not permanently frozen.

Example lifecycle:

```text
Raw User Input
     ↓
RequirementSet v1 DRAFT
     ↓
APPROVED
     ↓
engineering work
```

If the user changes a requirement:

```text
ChangeRequest
     ↓
RequirementSet v2
     ↓
dependency impact analysis
     ↓
affected artifacts become STALE
```

Old requirement versions remain readable.

---

# 10. PROVENANCE CLASSIFICATION

Important values must carry provenance.

Canonical classifications:

```text
USER_FACT
VERIFIED_FACT
USER_REQUIREMENT
ENGINEERING_ASSUMPTION
AI_PROPOSAL
CALCULATED_VALUE
SIMULATION_RESULT
VERIFIER_RESULT
```

Rule:

```text
FACT != ASSUMPTION != PROPOSAL
```

A value does not become a fact simply because another agent repeated it.

---

# 11. PROJECT STATE

Project State is the source of truth.

Suggested local storage:

```text
~/.local/share/remote-uav/
└── projects/
    └── UAV-PROJ-000001/
        ├── project.json
        ├── requirements/
        ├── architecture/
        ├── propulsion/
        ├── power/
        ├── sensors/
        ├── px4/
        ├── missions/
        ├── software/
        ├── simulation/
        ├── tests/
        ├── tasks/
        ├── ai_runs/
        ├── evidence/
        └── reports/
```

SQLite stores metadata/state/relationships.  
Filesystem stores large artifacts, source, documents, logs, telemetry, and evidence.

A server database is not required for the initial local-first architecture.

---

# 12. ARTIFACT MODEL

Agent output must not immediately become approved Project State.

Lifecycle:

```text
DRAFT
  ↓
REVIEWED
  ↓
VALIDATED
  ↓
APPROVED
```

Other states:

```text
REJECTED
STALE
SUPERSEDED
```

Every important artifact must have:

```text
artifact_id
version
type
producer task
input dependency versions
hash
status
validation result
created timestamp
```

---

# 13. ARTIFACT LINEAGE AND INVALIDATION

Dependency lineage is mandatory.

Example:

```text
Requirement v1
      ↓
Mass Budget v2
      ↓
Power v1
      ↓
Battery v1
```

If a new requirement version invalidates Mass Budget:

```text
Mass Budget v2 → STALE
Power v1       → STALE
Battery v1     → STALE
```

Unrelated artifacts remain unchanged.

An artifact whose required parent is STALE cannot be accepted as current.

This mechanism prevents old results from silently remaining valid after requirement/design changes.

---

# 14. TASK CONTRACT

Every task must have a bounded contract.

Minimum fields:

```text
task_id
project_id
title
role
priority
dependencies
input_artifacts
output_schema
validators
review_required
attempt
max_attempts
status
```

Canonical task states:

```text
CREATED
BLOCKED
READY
QUEUED
ASSIGNED
RUNNING
RESULT_PROPOSED
REVIEWING
VALIDATING
DONE
REWORK
FAILED
INTERRUPTED
NEEDS_HUMAN
CANCELLED
```

Tasks must persist across application restart.

A task that was `RUNNING` during an unexpected process stop becomes `INTERRUPTED` on recovery.

---

# 15. TASK DAG

The task system is a directed acyclic graph.

Example:

```text
Requirements
    │
    ├─────────────┐
    ▼             ▼
Mass Budget   Mission Profile
    │             │
    └──────┬──────┘
           ▼
      Propulsion
       ┌───┴───┐
       ▼       ▼
     Power   Airframe
       │       │
       └───┬───┘
           ▼
      Architecture
           │
   ┌───────┼────────┐
   ▼       ▼        ▼
  PX4   Software Simulation
```

Cycle detection is deterministic and mandatory before a plan can execute.

---

# 16. ORCHESTRATOR BOUNDARY

The Orchestrator creates a `TaskPlanProposal`.

It does not directly create executable tasks.

```text
Orchestrator
     ↓
TaskPlanProposal
     ↓
PlanValidator
     ↓
Approved Task DAG
```

Plan Validator checks:

```text
unique task IDs
known roles
known schemas
dependency existence
DAG acyclicity
tool permission validity
resource policy
task-count limits
dependency-depth limits
retry policy
```

A hallucinated plan is rejected before reaching Scheduler.

---

# 17. SCHEDULER

Scheduler is deterministic, not AI-driven.

It decides whether a READY task can run based on:

```text
dependency completion
priority
available execution slots
RAM / VRAM policy
provider health
model state
current resource mode
```

Scheduler must implement backpressure.

No unbounded worker spawning is allowed.

---

# 18. CONTEXT BUILDER

Agents do not receive entire project history.

Context Builder selects only:

```text
trusted role policy
task contract
relevant approved requirements
relevant approved parent artifacts
relevant project facts
previous review issues
previous deterministic validation failures
explicitly retrieved project documents
```

Retrieved documents are treated as untrusted project data, not system instructions.

Policy hierarchy:

```text
SYSTEM POLICY
      >
ROLE POLICY
      >
TASK CONTRACT
      >
APPROVED PROJECT STATE
      >
RETRIEVED DOCUMENT CONTENT
```

---

# 19. STRUCTURED AGENT RESULT

Primary Agent output is structured, not free-form prose.

Minimum logical result:

```json
{
  "status": "PROPOSED",
  "claims": [],
  "assumptions": [],
  "calculations": [],
  "artifacts": [],
  "risks": [],
  "missing_information": [],
  "open_questions": []
}
```

Human-readable prose is presentation output only.

Malformed model output must fail as `MODEL_INVALID_OUTPUT`, not corrupt Project State.

---

# 20. REVIEW MODEL

Reviewer creates issues. Reviewer does not directly modify the producer's artifact.

Example issue:

```json
{
  "issue_id": "ISSUE-0041",
  "severity": "HIGH",
  "category": "ENERGY",
  "field": "battery.capacity_wh",
  "reason": "Available energy does not satisfy endurance constraint.",
  "required_action": "Recalculate energy budget."
}
```

Issues require a stable fingerprint/root-cause identity so repeated identical review findings do not create unlimited duplicate tasks.

---

# 21. DETERMINISTIC ENGINEERING VALIDATION

Anything that can be verified reliably with code should be verified with code.

Initial calculator/validator scope may include:

```text
schema validation
finite-number checks
unit validation
mass budget
battery energy
energy reserve
thrust-to-weight
mission distance
mission duration
workspace bounds
constraint consistency
```

Do not attempt to build full CFD, full motor electromagnetic simulation, or full battery electrochemistry in V8 baseline.

---

# 22. TOOL AND SANDBOX POLICY

Agent tool access is role-specific and allowlisted.

Examples:

Requirements role:

```text
project_read
artifact_read
draft_write
```

Engineering role:

```text
project_read
calculator
artifact_read
draft_write
```

Software role:

```text
workspace_read
workspace_write
allowlisted_test_runner
```

Reviewer:

```text
artifact_read
project_read
issue_create
```

Forbidden by default:

```text
unrestricted shell
arbitrary filesystem path
arbitrary network access
direct PX4 adapter
direct raw MAVSDK command
```

Agent-generated code changes must occur in an isolated task workspace and become merge candidates only after tests/review/validation.

---

# 23. UAV BRIDGE

The AI layer never calls PX4 directly.

Required boundary:

```text
Approved Project State
        ↓
Simulation / Mission Proposal
        ↓
UAV Bridge
        ↓
Existing Remote UAV validation
        ↓
Capability / Mission Runtime
        ↓
PX4 SITL / Gazebo
```

The UAV Bridge is an adapter and policy boundary, not a replacement runtime.

The existing capability and mission verifier semantics must remain intact.

---

# 24. SIMULATION FEEDBACK

Verifier failures become normalized issues.

Example:

```text
position_error
      ↓
NAVIGATION issue
      ↓
PX4 / Simulation engineering rework
```

Failure feedback is routed only to relevant roles.

Do not broadcast every failure to every agent.

Simulation feedback is a separate loop from engineering review.

```text
Engineering Loop:
Agent → Review → Engineering Validator → Rework

Simulation Loop:
Approved Project → PX4/Gazebo → Verifier → Simulation Issue → Targeted Rework
```

---

# 25. FAILURE TAXONOMY

Operational failures and engineering failures must never be conflated.

Canonical categories:

```text
INFRASTRUCTURE
PROVIDER
RESOURCE
MODEL
TOOL
PLAN
TASK
REVIEW
VALIDATION
SIMULATION
VERIFICATION
USER_INPUT
```

Example distinctions:

```text
OLLAMA_TIMEOUT
→ provider/operational recovery

ENERGY_BUDGET_FAILED
→ engineering rework

POSITION_VERIFICATION_FAILED
→ simulation feedback

MISSING_CRITICAL_REQUIREMENT
→ user input required
```

---

# 26. ANTI-LOOP POLICY

No infinite retry is permitted.

Initial conservative defaults:

```yaml
ai:
  max_parallel_agents: 1
  max_loaded_models: 1

planner:
  max_tasks_per_plan: 30
  max_dependency_depth: 12

task:
  max_attempts: 3
  timeout_s: 300

review:
  max_rework_attempts: 2

simulation:
  max_retry_attempts: 2

project:
  max_rework_cycles: 5
```

When a limit is reached:

```text
→ NEEDS_HUMAN
```

Never:

```text
while not pass:
    retry_forever()
```

Limits may be increased later only after benchmark evidence supports the change.

---

# 27. RESOURCE MODES

Ollama and Gazebo may compete for local CPU/RAM/GPU resources.

The architecture therefore supports resource modes:

```text
AI_HEAVY
SIMULATION_HEAVY
BALANCED
```

Initial implementation may use a simpler rule:

```text
Do not start a new heavy AI inference while Gazebo/PX4 simulation startup is resource-critical.
```

On constrained machines, simulation may pause new AI task dispatch and optionally release the local model from memory.

---

# 28. HUMAN GATES

Default target autonomy:

```text
MEDIUM
```

System may automatically:

```text
schedule ready tasks
perform deterministic calculations
perform AI review
retry bounded transient provider failures
run approved simulation
perform bounded rework
```

User intervention is required for:

```text
critical missing requirement
requirement change
major architecture trade-off
conflicting user requirements
retry exhaustion
safety/policy boundary conflict
```

---

# 29. CRASH RECOVERY

Project/task state must survive process termination.

On restart:

```text
RUNNING → INTERRUPTED
```

The scheduler may create a new bounded attempt.

Do not attempt to continue inside a partially streamed LLM response.

---

# 30. STORAGE MODEL

Initial deployment is a modular monolith.

Use:

```text
SQLite     = metadata, state, relationships, indexes
Filesystem = documents, code, logs, artifacts, telemetry, evidence
Ollama     = separate local process/runtime
PX4/Gazebo = existing simulation processes/containers
```

No PostgreSQL, Kubernetes, distributed worker system, or event-bus microservice architecture is required for V8.

---

# 31. LOCAL RAG POLICY

Local RAG is **not** on the initial critical path.

Initial context retrieval:

```text
Project State
Artifact relationships
Direct file retrieval
Explicit metadata
```

Only after this is stable may a later phase add:

```text
Local embedding model
Vector index
Semantic retrieval
```

RAG must be benchmarked before becoming required infrastructure.

---

# 32. EXPLICIT NON-GOALS FOR V8 FOUNDATION

Do not implement these early:

```text
cloud LLM providers
physical UAV execution
direct AI flight control
20 agents running concurrently
multiple loaded models by default
full autonomous internet research
full CFD
full battery electrochemistry simulation
automatic component purchasing
Kubernetes
distributed workers
microservice decomposition
unrestricted agent shell
agent direct merge into production source
unbounded self-reflection
unbounded retries
```

---

# 33. IMPLEMENTATION STYLE

Use a **modular monolith**.

Suggested top-level AI tree:

```text
ai/
├── contracts/
├── providers/
├── model_manager/
├── project/
├── artifacts/
├── tasks/
├── engine/
├── orchestrator/
├── roles/
├── validators/
├── tools/
├── memory/
├── evidence/
└── schemas/
```

Do not mix the new AI orchestration layer into existing flight capability implementation files unless the UAV Bridge explicitly requires a small integration point.

---

# 34. WORK-PACKAGE RULE

Each implementation task must be small enough to test independently.

Allowed rough sizes:

```text
S = hours
M = approximately 1–2 focused development days
L = MUST be split before implementation
XL = FORBIDDEN
```

A task is too large if it combines multiple major state machines, multiple persistence families, or multiple trust boundaries.

Example forbidden task:

```text
"Implement Orchestrator + Scheduler + Agent Engine"
```

Required split:

```text
Task DAG
Scheduler
Agent Engine
Orchestrator
```

---

# 35. DEFINITION OF DONE FOR EVERY WORK PACKAGE

A work package is not complete because "the code is written."

Minimum DoD:

```text
implementation complete
new unit tests added
invalid input tested
expected failure mode tested
existing regression suite passes
no unbounded retry introduced
no unbounded in-memory collection introduced
schema/docs updated when applicable
```

Additional requirements:

```text
Persistence change → restart/recovery test
Agent feature      → MockProvider test
Resource feature   → hard-limit test
UAV integration    → verifier-backed acceptance result
```

Do not start the next dependent work package while the current gate is red.

---

# 36. CHECKPOINT POLICY

Canonical checkpoints:

```text
CP0  Existing UAV baseline
CP1  Contracts stable
CP2  Local provider stable
CP3  Project persistence stable
CP4  Artifact lineage stable
CP5  Task/DAG stable
CP6  Single-agent execution stable
CP7  Engineering validation stable
CP8  Minimal multi-agent orchestration stable
CP9  UAV Bridge stable
CP10 End-to-end simulation-feedback loop stable
```

If a later checkpoint becomes structurally unstable, roll back to the last green checkpoint rather than endlessly stacking patches.

---

# 37. GOLDEN TEST SUITE

Maintain deterministic/golden scenarios such as:

```text
GOLDEN-001 valid simple UAV requirement
GOLDEN-002 missing critical requirement
GOLDEN-003 infeasible endurance requirement
GOLDEN-004 intentionally wrong battery arithmetic
GOLDEN-005 cyclic task plan
GOLDEN-006 Ollama unavailable
GOLDEN-007 invalid model JSON
GOLDEN-008 simulation verifier failure
GOLDEN-009 requirement version invalidates descendants
GOLDEN-010 restart while task RUNNING
```

Track at least:

```text
requirement extraction quality
plan validity
unsupported claims
validator catch rate
rework count
task attempts
context size
wall-clock runtime
RAM usage
VRAM usage when available
```

---

# 38. EXECUTION ROADMAP

## PHASE 0 — Baseline Freeze

Purpose: protect current working UAV runtime.

Work:

```text
B0.1 record current version
B0.2 run complete regression
B0.3 run Single Flight acceptance
B0.4 run Mission acceptance
B0.5 capture source/package hash
B0.6 record runtime architecture boundary
```

Gate:

```text
all expected tests green
acceptance green
no unexplained resource leak
baseline hash recorded
```

## PHASE 1 — Contracts + Failure Model

Work:

```text
C1.1 canonical IDs
C1.2 task states
C1.3 artifact states
C1.4 provenance classes
C1.5 AgentResult
C1.6 ReviewResult
C1.7 ValidationResult
C1.8 Failure taxonomy
C1.9 serialization tests
```

No Ollama required.

## PHASE 2 — Local Provider

Work:

```text
P2.1 LocalAIProvider
P2.2 MockProvider
P2.3 request/response contracts
P2.4 Ollama health
P2.5 model listing
P2.6 generate
P2.7 timeout handling
P2.8 malformed output handling
P2.9 bounded provider retry
P2.10 minimal Model Manager
```

No multi-agent yet.

## PHASE 3 — Project State

Work:

```text
S3.1 SQLite project schema
S3.2 filesystem project layout
S3.3 create/open project
S3.4 RequirementSet
S3.5 requirement versions
S3.6 facts
S3.7 assumptions
S3.8 decisions
S3.9 transaction wrapper
S3.10 restart tests
```

## PHASE 4 — Artifact Registry + Lineage

Work:

```text
A4.1 artifact schema
A4.2 artifact versions
A4.3 dependency edges
A4.4 hashing
A4.5 state transitions
A4.6 STALE propagation
A4.7 SUPERSEDED handling
A4.8 graph tests
```

## PHASE 5 — Task Engine

Work:

```text
T5.1 task persistence
T5.2 state machine
T5.3 dependencies
T5.4 DAG cycle detection
T5.5 READY resolver
T5.6 priority queue
T5.7 attempt counter
T5.8 timeout
T5.9 interrupted recovery
T5.10 cancellation
T5.11 concurrency limit
```

## PHASE 6 — Single Agent Engine

Work:

```text
G6.1 Role Registry
G6.2 AgentInstance
G6.3 Task → Agent mapping
G6.4 minimal Context Builder
G6.5 schema-enforced output
G6.6 Agent run persistence
G6.7 context manifest
G6.8 result → DRAFT artifact
```

Start with only a few roles.

## PHASE 7 — Deterministic Engineering Trust

Work:

```text
V7.1 schema validation
V7.2 finite-number checks
V7.3 units
V7.4 mass budget
V7.5 battery energy
V7.6 reserve calculation
V7.7 thrust-to-weight
V7.8 mission distance
V7.9 constraint framework
V7.10 validation issue schema
```

Implement only calculators needed by current use cases.

## PHASE 8 — Minimal Orchestrator

Work:

```text
O8.1 TaskPlanProposal
O8.2 Orchestrator role
O8.3 PlanValidator
O8.4 known-role validation
O8.5 known-schema validation
O8.6 dependency validation
O8.7 resource-budget validation
O8.8 persist approved DAG
```

## PHASE 9 — Review + Feedback

Work:

```text
R9.1 issue schema
R9.2 reviewer role
R9.3 review task generation
R9.4 issue → owner routing
R9.5 rework task
R9.6 max-rework gate
R9.7 duplicate issue suppression
```

## PHASE 10 — Tools / Sandbox

Work:

```text
X10.1 Tool Registry
X10.2 role allowlists
X10.3 timeout wrapper
X10.4 path restriction
X10.5 structured tool results
X10.6 audit log
```

Add Software Agent workspace only after this layer is stable.

## PHASE 11 — UAV Bridge

Work:

```text
U11.1 simulation proposal schema
U11.2 project → mission mapping
U11.3 bridge validation
U11.4 capability allowlist
U11.5 mission allowlist
U11.6 invoke existing runtime
U11.7 link UAV run back to project
```

## PHASE 12 — Simulation Feedback

Work:

```text
F12.1 verifier-result parser
F12.2 simulation issue taxonomy
F12.3 issue → engineering role mapping
F12.4 lineage invalidation
F12.5 rework task generation
F12.6 bounded simulation retry
F12.7 NEEDS_HUMAN transition
```

## PHASE 13 — AI Workspace UI

Only after backend end-to-end is green.

Routes:

```text
/         existing Single Flight
/mission  existing Mission Builder
/projects new Project Manager
/ai       new AI Engineering Workspace
```

Initial UI:

```text
project status
requirements
task DAG
agent status
issues
artifacts
simulation status
resource status
```

## PHASE 14 — Optional Local RAG

Only when direct project retrieval is demonstrated to be insufficient.

---

# 39. MVP BOUNDARIES

## MVP 1 — No PX4 integration

Prove:

```text
User requirement
      ↓
Requirement Agent
      ↓
Project State
      ↓
Orchestrator
      ↓
small validated Task DAG
      ↓
Systems Agent
      ↓
Reviewer
      ↓
Deterministic Validator
      ↓
Approved Artifacts
```

## MVP 2 — Closed simulation loop

Then add:

```text
Approved Project
      ↓
Simulation Proposal
      ↓
UAV Bridge
      ↓
MISSION-001
      ↓
PX4/Gazebo
      ↓
Independent Verifier
      ↓
Targeted feedback
```

Do not attempt the full end-to-end system before MVP 1 is stable.

---

# 40. FINAL PROJECT STATUS MODEL

Do not use a vague single `PASS`.

Example:

```text
Requirements            APPROVED
Engineering Artifacts    VALIDATED
Software Tests           PASS
PX4 Integration          VALIDATED
Simulation               VERIFIED
Mission                  VERIFIED

Open Assumptions         N
Unverified Claims        N
Blocking Issues          N
```

For the current simulation-only scope, an appropriate highest-level status is:

```text
ENGINEERING_VALIDATED
+
SIMULATION_VERIFIED
```

Do not claim:

```text
REAL_WORLD_VERIFIED
PHYSICALLY_VERIFIED
```

without a separate future real-hardware program and baseline.

---

# 41. ARCHITECTURE DECISIONS THAT ARE FROZEN

The following decisions require an explicit future Change Request to alter:

```text
ADR-001 Local AI only; no cloud LLM in baseline.
ADR-002 OllamaProvider + MockProvider first.
ADR-003 Project State is the source of truth.
ADR-004 Requirements are versioned.
ADR-005 Artifacts are versioned and dependency-tracked.
ADR-006 Orchestrator proposes plans; PlanValidator approves plans.
ADR-007 Scheduler is deterministic.
ADR-008 Agents use isolated contexts.
ADR-009 Agent result is DRAFT, never immediate project truth.
ADR-010 AI review is advisory; deterministic checks remain authoritative.
ADR-011 AI never directly controls PX4/MAVSDK/actuators.
ADR-012 Existing UAV verifier remains simulation verdict authority.
ADR-013 Retries are bounded and terminate in NEEDS_HUMAN.
ADR-014 Initial implementation is a modular monolith.
ADR-015 SQLite + filesystem are the initial storage architecture.
ADR-016 Local RAG is deferred until the non-RAG foundation is stable.
ADR-017 UAV Runtime Core is not rewritten for AI integration.
ADR-018 Resource scheduling must account for Ollama + Gazebo contention.
ADR-019 Development proceeds by gated checkpoints.
ADR-020 No work package may silently bypass its phase gate.
```

---

# 42. SOURCE-CODE BASELINE RECORD

This architecture baseline is frozen now.

The exact **target machine source snapshot** must also be frozen during PHASE 0.

Record these values on the target machine:

```text
CURRENT_PROJECT_PATH = <TO_FILL_ON_TARGET>
CURRENT_VERSION      = <TO_FILL_ON_TARGET>
GIT_COMMIT           = <TO_FILL_ON_TARGET_OR_N/A>
SOURCE_ARCHIVE       = <TO_FILL_ON_TARGET>
SOURCE_SHA256        = <TO_FILL_ON_TARGET>
REGRESSION_RESULT    = <TO_FILL_ON_TARGET>
SINGLE_FLIGHT_RESULT = <TO_FILL_ON_TARGET>
MISSION_RESULT       = <TO_FILL_ON_TARGET>
```

Do not invent these values from an older build.

The current target source tree, not a historical artifact, determines CP0.

---

# 43. CHANGE CONTROL

Any future proposed architecture change must be recorded in a Change Request:

```text
CR ID
baseline affected
reason
problem being solved
proposed change
dependencies affected
migration required?
new risks
tests required
rollback plan
approval
```

A change is not active until explicitly approved.

When approved:

```text
RUAV-AI-ARCH-V8.0-BL1
        ↓
approved CR
        ↓
RUAV-AI-ARCH-V8.0-BL2
```

Do not edit history to pretend the old baseline never existed.

---

# 44. NEXT AUTHORIZED ACTION

The only authorized next implementation sequence after accepting this baseline is:

```text
1. Complete PHASE 0 source baseline freeze on the actual target tree.
2. Confirm CP0 is GREEN.
3. Implement PHASE 1 Contracts + Failure Model.
4. Confirm CP1 is GREEN.
5. Only then begin PHASE 2 Local Provider.
```

Do not begin Orchestrator, RAG, multi-agent feedback, or UAV Bridge implementation before their dependencies are green.

---

# 45. BASELINE SUMMARY

The architecture can be summarized as:

```text
LOCAL MODEL
     ↓
CONTROLLED AGENT ENGINE
     ↓
VALIDATED TASK DAG
     ↓
VERSIONED PROJECT STATE
     ↓
AI REVIEW + DETERMINISTIC ENGINEERING VALIDATION
     ↓
APPROVED ARTIFACTS + LINEAGE
     ↓
UAV BRIDGE
     ↓
EXISTING REMOTE UAV CORE
     ↓
PX4 SITL / GAZEBO
     ↓
INDEPENDENT VERIFICATION
     ↓
TARGETED BOUNDED FEEDBACK
     ↓
ENGINEERING_VALIDATED + SIMULATION_VERIFIED PROJECT
```

**End of frozen baseline `RUAV-AI-ARCH-V8.0-BL1`.**
