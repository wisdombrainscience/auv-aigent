# C1.6 — ReviewResult Contract

Status: candidate for target gate.

## Baseline basis

The frozen architecture states that the AI Reviewer:

- performs semantic critique, contradiction detection, and unsupported-claim review;
- is advisory, not an authoritative deterministic validator;
- creates structured issues;
- does not directly modify the producer's artifact;
- requires stable issue/root-cause identity so repeated identical findings can
  later be deduplicated.

The baseline's issue example contains:

`issue_id`, `severity`, `category`, `field`, `reason`, `required_action`.

C1.6 adds the explicitly required stable root-cause `fingerprint`.

## Minimum ReviewResult

C1.6 deliberately freezes only:

```json
{
  "issues": []
}
```

There is no review `PASS`, `FAIL`, `APPROVED`, artifact patch, or replacement
artifact field. An empty issue list means only that this advisory review
emitted no issues; it does not replace deterministic validation.

Each issue contains exactly:

```json
{
  "issue_id": "ISSUE-0041",
  "fingerprint": "stable-root-cause-key",
  "severity": "HIGH",
  "category": "ENERGY",
  "field": "battery.capacity_wh",
  "reason": "Available energy does not satisfy endurance constraint.",
  "required_action": "Recalculate energy budget."
}
```

`field` may be null for an issue that is not tied to a single artifact property.

## Duplicate control

C1.6 rejects duplicate issue IDs and duplicate fingerprints inside one
ReviewResult. Cross-review persistence/deduplication, fingerprint generation
policy, owner routing, and rework-task generation remain Phase 9 work.

## Deliberate boundaries

C1.6 does not enumerate severity/category vocabularies, mutate artifacts,
persist issues, route owners, create rework tasks, execute reviewers, or
implement retry loops.

It also does not add providers, Ollama, orchestration, RAG, or UAV execution.

## Authority boundary

AI Reviewer output remains advisory. Deterministic Engineering Validators are
authoritative for checks they implement, and the existing independent UAV
verifier remains the sole simulation PASS/FAIL authority.
