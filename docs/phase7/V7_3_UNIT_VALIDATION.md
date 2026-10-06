# V7.3 — Deterministic Unit Validation

## Scope

V7.3 is the third work package in frozen Phase 7 — Deterministic Engineering Trust.
It executes the task-declared `units` validator against one persisted artifact and
returns the existing C1.7 `ValidationResult` contract.

V7.3 does **not** convert units, does not persist validation results, and does not
change artifact or task lifecycle state. The frozen artifact transition order remains:

```text
DRAFT → REVIEWED → VALIDATED → APPROVED
```

## Canonical unit declaration contract

V7.3 validates unit declarations, not domain-schema completeness. A number is unit
checked when either:

1. it is inside an object carrying an explicit `unit` member; or
2. its field name ends with a known canonical suffix such as `_kg`, `_wh`,
   `_percent`, `_m_s`, or `_m_s2`.

Numbers without a unit annotation are not rejected by V7.3. Later domain calculators
(V7.4+) own the rule that a particular engineering field must exist and must have a
particular dimension.

Canonical spellings frozen for the current calculator roadmap are:

```text
1
%
kg
g
m
km
s
min
h
Wh
Ah
V
A
W
N
m/s
m/s^2
deg
rad
```

The validator performs no silent aliasing or conversion. Examples such as `KG`, `lb`,
`ft`, `mph`, `mAh`, and `percent` therefore fail when supplied as explicit unit values.

## Consistency rule

If a field suffix already declares a unit and the nested object also declares `unit`,
they must agree. For example:

```json
{"mass_kg": {"value": 2.1, "unit": "kg"}}
```

passes, while:

```json
{"mass_kg": {"value": 2100, "unit": "g"}}
```

fails. V7.3 does not convert `g` to `kg`; later deterministic calculators may perform
explicit conversions only if a later frozen contract authorizes them.

An explicit `{ "value": ..., "unit": ... }` quantity requires `value` to be a finite
number. A `unit` may also annotate other numeric fields such as `observed` and `limit`
without requiring a `value` member, which is compatible with the C1.7 validation-result
evidence shape.

## Bounded deterministic scan

Artifact bytes remain hash-verified by Phase 4 first. V7.3 then performs strict UTF-8
JSON decoding with duplicate-key and non-finite-constant rejection. The traversal is
iterative and bounded by:

```text
MAX_UNIT_SCAN_NODES = 100000
MAX_UNIT_SCAN_DEPTH = 128
MAX_UNIT_TEXT_CHARS = 16
```

This prevents unbounded recursive/reflection behavior.

## Task binding

The producer `TaskRecord` remains source of truth. V7.3 runs only when the task
contains:

```text
validators: [..., units, ...]
```

Missing/mismatched producer state, missing artifacts, or content-hash corruption are
operational conflicts and fail closed rather than being disguised as engineering FAIL.

## Result

One C1.7 check is returned:

```text
check_id = units
status   = PASS | FAIL
```

PASS evidence records the number of explicit unit annotations and canonical suffix
values checked. FAIL reasons contain deterministic field paths where relevant.

## Non-goals

V7.3 does not implement:

- V7.4 mass budget;
- battery-energy/reserve/thrust/mission calculators;
- unit conversion or alias normalization;
- dimensional completeness for domain-specific fields;
- V7.9 constraint framework;
- V7.10 validation issue schema;
- DRAFT → REVIEWED;
- REVIEWED → VALIDATED;
- validation-result persistence;
- artifact approval;
- provider/model calls;
- PX4/MAVSDK/Gazebo control.
