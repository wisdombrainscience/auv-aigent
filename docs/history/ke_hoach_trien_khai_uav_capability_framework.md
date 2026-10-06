# KẾ HOẠCH TRIỂN KHAI UAV CAPABILITY FRAMEWORK

## 1. Mục tiêu của giai đoạn đầu

Mục tiêu không phải là xây dựng ngay một hệ thống UAV tự chủ hoàn chỉnh, mà là:

> **Xây dựng và kiểm chứng một framework module hóa cho capability UAV, dùng `AltitudeHoldModule` làm module tham chiếu đầu tiên.**

Kết quả cuối giai đoạn này phải chứng minh được chuỗi:

```text
Requirement
    ↓
Structured Input
    ↓
Capability Module
    ↓
Execution
    ↓
Observation
    ↓
Verification
    ↓
PASS / FAIL
    ↓
Recovery
    ↓
Evidence + Report
```

Nếu chuỗi trên hoạt động ổn định, nó trở thành nền để mở rộng:

```text
AltitudeHold
    ↓
PositionHold
    ↓
GoToTarget
    ↓
WaypointSequence
    ↓
MissionExecution
    ↓
AdaptiveMission
    ↓
Agent Orchestrator
```

---

## 2. Kiến trúc cần xây ngay từ đầu

Không nên bắt đầu bằng một file Python lớn.

Nên định nghĩa 5 lớp:

```text
┌─────────────────────────────────┐
│       Experiment Layer          │
│       ExperimentRunner          │
└───────────────┬─────────────────┘
                ↓
┌─────────────────────────────────┐
│       Capability Layer          │
│      AltitudeHoldModule         │
└───────────────┬─────────────────┘
                ↓
┌─────────────────────────────────┐
│         Core Framework          │
│ Validator / Observer /          │
│ Verifier / Recovery / Result    │
└───────────────┬─────────────────┘
                ↓
┌─────────────────────────────────┐
│          UAV Interface          │
│ Abstract UAV Backend API        │
└───────────────┬─────────────────┘
                ↓
┌─────────────────────────────────┐
│        Backend / Simulator      │
│ Mock → SITL → later real UAV    │
└─────────────────────────────────┘
```

Đây là kiến trúc cần giữ xuyên suốt dự án.

---

# 3. Giai đoạn 0 — Viết specification trước code

Đây là bước đầu tiên và không nên bỏ qua.

Tạo tài liệu:

```text
ALTITUDE_HOLD_SPEC.md
```

Trong đó phải chốt:

| Nội dung | Giá trị MVP |
|---|---|
| Capability | Maintain Altitude |
| Input chính | target altitude |
| Tolerance | configurable |
| Duration | configurable |
| Backend ban đầu | Mock |
| Backend tiếp theo | SITL |
| Output | PASS / FAIL / REJECTED |
| Evidence | telemetry + metrics + report |
| Recovery | retry/abort cơ bản |

Đồng thời xác định lifecycle:

```text
CREATED
  ↓
VALIDATING
  ↓
READY
  ↓
EXECUTING
  ↓
OBSERVING
  ↓
VERIFYING
  ↓
PASS / FAIL
```

Nếu lỗi:

```text
FAIL
 ↓
RECOVERING
 ↓
RETRY / ABORT
```

### Deliverable

```text
docs/
└── ALTITUDE_HOLD_SPEC.md
```

### Điều kiện hoàn thành

Trước khi code, phải trả lời được rõ:

```text
Module nhận gì?
Module trả gì?
Khi nào được chạy?
Khi nào PASS?
Khi nào FAIL?
Lỗi được biểu diễn thế nào?
Evidence nằm ở đâu?
```

---

# 4. Giai đoạn 1 — Xây `BaseCapabilityModule`

Đây là phần nền tảng nhất.

Thiết kế base interface:

```python
class BaseCapabilityModule:
    def validate_input(self):
        ...

    def check_preconditions(self):
        ...

    def execute(self):
        ...

    def observe(self):
        ...

    def verify(self):
        ...

    def recover(self):
        ...

    def report(self):
        ...
```

Nhưng quan trọng hơn code là **contract**.

Mỗi module sau này bắt buộc phải cung cấp:

```text
module_id
module_name
capability
level
input_schema
output_schema
dependencies
preconditions
failure_codes
verification_rules
```

Ví dụ:

```text
module_id:
UAV-L1-ALT-001

capability:
maintain_altitude

level:
1
```

### Deliverable

```text
core/
├── base_module.py
├── lifecycle.py
└── module_contract.py
```

### Acceptance

Một dummy module phải chạy được:

```text
Input
↓
Validate
↓
Execute
↓
Verify
↓
Result
```

mà chưa cần UAV.

---

# 5. Giai đoạn 2 — Chuẩn hóa Input / Output

Đây là bước cực kỳ quan trọng nếu sau này muốn Agent gọi module.

Input không nên là lời nói tự do.

Ví dụ:

```json
{
  "experiment_id": "ALT-001",
  "target_altitude_m": 5.0,
  "tolerance_m": 0.3,
  "duration_s": 20,
  "required_valid_ratio": 0.95
}
```

Output chuẩn:

```json
{
  "experiment_id": "ALT-001",
  "module": "AltitudeHoldModule",
  "status": "PASS",
  "failure_code": null,
  "metrics": {
    "mean_error_m": 0.04,
    "max_error_m": 0.22,
    "rmse_m": 0.09,
    "valid_ratio": 0.98
  }
}
```

Nếu input sai:

```json
{
  "status": "REJECTED",
  "failure_code": "INVALID_TARGET_ALTITUDE"
}
```

Nếu chạy nhưng không đạt:

```json
{
  "status": "FAIL",
  "failure_code": "ALTITUDE_ERROR_EXCEEDED"
}
```

### Cần phân biệt rõ

```text
REJECTED
= chưa được phép chạy.

FAIL
= đã chạy nhưng capability không đạt.

PASS
= capability đạt yêu cầu.
```

### Deliverable

```text
schemas/
├── capability_request.py
└── capability_result.py
```

---

# 6. Giai đoạn 3 — Xây `UAVInterface`

Không được để `AltitudeHoldModule` phụ thuộc trực tiếp vào một simulator cụ thể.

Interface chung:

```text
connect()

is_connected()

get_vehicle_state()

get_altitude()

set_altitude_target()

get_health()

stop()
```

Kiến trúc:

```text
AltitudeHoldModule
        ↓
UAVInterface
        ↓
┌───────┼────────┐
↓       ↓        ↓
Mock    SITL     Future Backend
```

### Lợi ích

Khi đổi backend:

```text
Mock
→ simulator
→ hardware
```

logic module không phải viết lại.

### Deliverable

```text
interfaces/
└── uav_interface.py
```

---

# 7. Giai đoạn 4 — Mock backend trước

Trước khi nối simulator, tự tạo dữ liệu mô phỏng.

Case PASS:

```text
5.02
5.06
4.97
5.01
4.94
...
```

Case FAIL:

```text
4.31
5.61
4.43
5.52
...
```

Case telemetry lost:

```text
5.01
5.02
None
None
5.04
```

Mock backend cho phép kiểm tra framework mà không bị nhiễu bởi simulator.

### Test cần có

| Case | Expected |
|---|---|
| normal | PASS |
| large altitude error | FAIL |
| invalid input | REJECTED |
| temporary telemetry loss | RECOVERY |
| permanent telemetry loss | FAIL |

### Deliverable

```text
adapters/
└── mock_uav_adapter.py
```

---

# 8. Giai đoạn 5 — Xây `InputValidator`

Validator phải độc lập.

Nó chỉ kiểm tra input.

Ví dụ:

```text
target_altitude_m > 0

tolerance_m > 0

duration_s > 0

0 < required_valid_ratio <= 1
```

Nếu sai:

```text
REJECTED
```

Không gửi gì xuống UAV.

### Deliverable

```text
core/
└── validator.py
```

### Unit test

```text
target = -1
→ INVALID_TARGET_ALTITUDE

duration = 0
→ INVALID_DURATION
```

---

# 9. Giai đoạn 6 — Xây `PreconditionChecker`

Khác với input validator.

Ví dụ request hoàn toàn hợp lệ:

```text
target = 5 m
```

nhưng UAV không có telemetry.

Khi đó:

```text
PRECONDITION_FAILED
```

Precondition đầu tiên chỉ cần:

```text
backend connected?
vehicle state available?
altitude available?
health acceptable?
```

### Output

```json
{
  "ready": false,
  "reason": "ALTITUDE_SOURCE_UNAVAILABLE"
}
```

### Deliverable

```text
core/
└── preconditions.py
```

---

# 10. Giai đoạn 7 — Xây `TelemetryObserver`

Observer cần hoàn toàn tách khỏi verifier.

Nhiệm vụ duy nhất:

> Ghi lại những gì xảy ra.

Dữ liệu:

```text
timestamp
target_altitude
actual_altitude
error
vehicle_state
health_state
```

Ví dụ:

```csv
timestamp,target,actual,error
0.00,5.0,4.91,-0.09
0.10,5.0,4.96,-0.04
0.20,5.0,5.03,0.03
```

### Vì sao tách riêng?

Để sau này có thể:

```text
Replay experiment
```

mà không cần chạy UAV lại.

Verifier chỉ cần đọc telemetry cũ.

### Deliverable

```text
core/
└── observer.py
```

---

# 11. Giai đoạn 8 — Xây `AltitudeVerifier`

Đây là trái tim của việc chứng minh module đúng hay sai.

Requirement:

```text
target = H

tolerance = T
```

Tại sample `i`:

```text
error_i = actual_i - target
```

Absolute error:

```text
|error_i|
```

Valid sample:

```text
|error_i| <= tolerance
```

Valid ratio:

```text
valid samples
──────────────
total samples
```

Có thể tính thêm:

```text
mean error
mean absolute error
max error
RMSE
```

Ví dụ acceptance:

```text
duration >= required duration

AND

valid_ratio >= 0.95

AND

no critical failure
```

### Lưu ý về metric

Nếu yêu cầu:

```text
max_error <= 0.3
```

và đồng thời:

```text
95% sample nằm trong ±0.3
```

thì về logic `max_error <= 0.3` đã khiến 100% sample hợp lệ.

Do đó nên dùng:

```text
valid_ratio >= 95%
AND
max_error <= emergency_threshold
```

Ví dụ:

```text
tolerance = 0.3 m
required valid ratio = 95%
hard max error = 0.6 m
```

### Deliverable

```text
capabilities/
└── altitude_hold/
    └── verifier.py
```

---

# 12. Giai đoạn 9 — Xây `AltitudeHoldModule`

Đến đây mới ghép mọi component thành module thực.

```text
AltitudeHoldModule
│
├── Validator
├── PreconditionChecker
├── UAVInterface
├── Observer
├── Verifier
├── Recovery
└── Reporter
```

Lifecycle:

```text
Request
   ↓
Validate
   ↓
Check Preconditions
   ↓
Send Target
   ↓
Observe
   ↓
Verify
   ↓
PASS / FAIL
```

`AltitudeHoldModule` chỉ orchestrate.

Không nhét toàn bộ logic vào một function.

### Deliverable

```text
capabilities/
└── altitude_hold/
    ├── module.py
    ├── schema.py
    └── verifier.py
```

---

# 13. Giai đoạn 10 — Failure taxonomy

Ngay từ module đầu tiên phải chuẩn hóa lỗi.

| Failure Code | Ý nghĩa |
|---|---|
| `INVALID_INPUT` | input sai |
| `PRECONDITION_FAILED` | UAV chưa sẵn sàng |
| `BACKEND_CONNECTION_LOST` | mất kết nối |
| `TELEMETRY_LOST` | mất dữ liệu |
| `EXECUTION_TIMEOUT` | quá thời gian |
| `ALTITUDE_PERFORMANCE_FAILED` | giữ độ cao không đạt |
| `SYSTEM_HEALTH_ERROR` | backend báo lỗi |
| `VERIFICATION_ERROR` | verifier không đánh giá được |

Sau này Agent nhìn failure code thay vì đọc text.

---

# 14. Giai đoạn 11 — Recovery

Ban đầu recovery chỉ cần rule-based.

Ví dụ:

```text
TELEMETRY_LOST
→ retry 1 lần

CONNECTION_LOST
→ reconnect

INVALID_INPUT
→ không retry

PRECONDITION_FAILED
→ abort

ALTITUDE_PERFORMANCE_FAILED
→ FAIL
```

Flow:

```text
Execution
   ↓
Failure
   ↓
Classifier
   ↓
Recovery policy
   ↓
Retry?
 /    \
YES    NO
 │      │
Retry  Abort
```

Lưu lại toàn bộ attempt:

```text
Attempt 1
FAIL

Recovery
SUCCESS

Attempt 2
PASS
```

Đây là nơi framework `attempt → verification → recovery` có thể được áp dụng trực tiếp.

---

# 15. Giai đoạn 12 — `ExperimentRunner`

Không để developer phải tự chạy từng component.

Có lệnh dạng:

```text
run experiment ALT-001
```

ExperimentRunner:

```text
Load request
    ↓
Create module
    ↓
Execute
    ↓
Collect telemetry
    ↓
Verify
    ↓
Generate result
    ↓
Generate report
```

### Deliverable

```text
experiments/
└── runner.py
```

---

# 16. Giai đoạn 13 — Chuẩn hóa artifact

Mỗi experiment:

```text
runs/
└── ALT-001/
    ├── request.json
    ├── metadata.json
    ├── telemetry.csv
    ├── events.jsonl
    ├── metrics.json
    ├── result.json
    └── report.html
```

Ý nghĩa:

```text
request.json
→ test muốn gì?

telemetry.csv
→ UAV thực tế làm gì?

metrics.json
→ tính được gì?

result.json
→ PASS/FAIL?

events.jsonl
→ trong lúc chạy xảy ra gì?

report.html
→ con người xem.
```

Đây là **audit trail** của experiment.

---

# 17. Giai đoạn 14 — Dashboard / report

Đây là thứ cần show leader.

Một report nên gồm:

```text
Experiment ID
Module
Backend
Timestamp
```

Input:

```text
Target altitude     5.0 m
Tolerance           ±0.3 m
Duration            20 s
```

Biểu đồ:

```text
Altitude
  ↑

5.6 ───────── Hard limit
5.3 ───────── Normal upper tolerance

         ╭──╮
5.0 ─────┼──┼──────── Target
     ╭───╯  ╰─╮

4.7 ───────── Normal lower tolerance
4.4 ───────── Hard limit

    ─────────────────→ Time
```

Metrics:

| Metric | Requirement | Actual | Result |
|---|---:|---:|---|
| Duration | ≥20 s | 20 s | PASS |
| Valid ratio | ≥95% | 98.2% | PASS |
| Hard error | ≤0.6 m | 0.31 m | PASS |
| Health | no critical fault | OK | PASS |

Cuối report:

```text
OVERALL
PASS
```

Nếu recovery xảy ra:

```text
Attempts: 2

Attempt 1 → FAIL
Recovery → SUCCESS
Attempt 2 → PASS
```

---

# 18. Giai đoạn 15 — Chạy test matrix

Không chạy một case rồi kết luận thành công.

Nên có test matrix:

| ID | Scenario | Expected |
|---|---|---|
| ALT-001 | nominal 2 m | PASS |
| ALT-002 | nominal 5 m | PASS |
| ALT-003 | nominal 10 m | PASS |
| ALT-004 | invalid target | REJECTED |
| ALT-005 | large oscillation | FAIL |
| ALT-006 | telemetry loss temporary | RECOVERY/PASS |
| ALT-007 | telemetry loss permanent | FAIL |
| ALT-008 | backend unavailable | PRECONDITION FAIL |
| ALT-009 | execution timeout | FAIL |

Sau này report tổng hợp:

```text
9 tests

PASS             4
EXPECTED FAIL    3
REJECTED         1
RECOVERY PASS    1
```

Lưu ý: “FAIL test” không phải hệ thống thất bại.

Nếu test được thiết kế để hệ thống phải phát hiện lỗi và nó phát hiện đúng, thì framework test **PASS**.

---

# 19. Giai đoạn 16 — Nối SITL

Chỉ làm sau khi Mock chạy ổn.

Backend đổi:

```text
MockAdapter
   ↓
SITLAdapter
```

Phần phía trên giữ nguyên:

```text
AltitudeHoldModule
Verifier
Observer
Result
Report
```

Đây chính là bài test cho abstraction.

Nếu đổi backend mà phải sửa quá nhiều code module thì architecture chưa tốt.

---

# 20. Giai đoạn 17 — Kiểm chứng tính tái lập

Một experiment phải chạy nhiều lần.

Ví dụ:

```text
ALT-SITL-005

10 repetitions
```

Thu:

```text
mean valid ratio
std deviation
mean RMSE
failure count
```

Mục đích:

> Kết quả có ổn định hay chỉ may mắn PASS một lần?

---

# 21. Giai đoạn 18 — Đánh giá framework, không chỉ UAV

Sau experiment, đánh giá hai chiều.

## Capability performance

```text
UAV giữ altitude có đạt requirement không?
```

## Framework performance

```text
Input validation có đúng không?

Precondition có đúng không?

Telemetry có đủ không?

Verifier có deterministic không?

Failure classification có đúng không?

Recovery có hoạt động không?

Report có tự sinh không?

Backend có thay thế được không?
```

Nếu capability PASS nhưng framework lộn xộn thì vẫn chưa đạt mục tiêu của dự án.

---

# 22. Giai đoạn 19 — Freeze Module Contract v1

Sau khi thử nghiệm ổn:

```text
Capability Module Contract v1
```

Chốt:

```text
Input schema
Output schema
Lifecycle
Failure schema
Verification interface
Artifact structure
Recovery interface
```

Từ đây tất cả module mới phải tuân theo chuẩn đó.

---

# 23. Giai đoạn 20 — Module thứ hai: `PositionHoldModule`

Không xây framework mới.

Tái sử dụng:

```text
BaseCapabilityModule
Validator
Observer
Recovery
Result
Report
UAVInterface
ExperimentRunner
```

Chỉ thêm:

```text
PositionHoldSchema
PositionHoldVerifier
PositionHoldModule
```

Input:

```text
target_x
target_y
target_z
tolerance
duration
```

Verify:

```text
position_error =
distance(actual, target)
```

Đây là bài kiểm tra đầu tiên xem framework có thực sự reusable hay không.

---

# 24. Module thứ ba: `GoToTargetModule`

Capability:

```text
Move UAV
from current position
to target position
```

Input:

```text
target position
arrival tolerance
timeout
```

Output:

```text
PASS / FAIL
arrival error
time to target
trajectory evidence
```

Nó bắt đầu sử dụng capability thấp hơn.

```text
GoToTarget
    ↓
position/localization
    ↓
control capability
```

---

# 25. Từ module sang Level

Khi có đủ:

```text
AltitudeHold
PositionHold
AttitudeHold
```

thì có thể xác nhận:

```text
Level 1 capability set
```

Khi thêm:

```text
GoToTarget
FollowTrajectory
GoalReached
```

thì hình thành:

```text
Level 2
```

Sau đó:

```text
TaskExecutor
SequenceManager
MissionManager
```

→ Level 3.

Và:

```text
EventDetector
SituationEvaluator
Replanning
Recovery
```

→ Level 4.

Cuối cùng:

```text
GoalInterpreter
TaskDecomposer
CapabilitySelector
Orchestrator
```

→ Level 5.

---

# 26. Khi nào mới đưa AI Agent vào?

Không nên đưa AI Agent vào giai đoạn `AltitudeHold`.

Agent nên xuất hiện khi hệ thống đã có nhiều capability ổn định.

Ví dụ registry:

```text
Capability Registry

altitude_hold
position_hold
go_to_target
takeoff
land
return_home
...
```

Agent nhận:

```text
Goal
```

rồi:

```text
Goal
 ↓
Task decomposition
 ↓
Capability selection
 ↓
Module execution
 ↓
Verification
```

Agent không trực tiếp điều khiển low-level.

---

# 27. Cấu trúc repository hoàn chỉnh

```text
uav_capability_framework/
│
├── docs/
│   ├── architecture.md
│   ├── module_contract.md
│   └── altitude_hold_spec.md
│
├── core/
│   ├── base_module.py
│   ├── lifecycle.py
│   ├── validator.py
│   ├── observer.py
│   ├── recovery.py
│   ├── result.py
│   └── errors.py
│
├── interfaces/
│   └── uav_interface.py
│
├── adapters/
│   ├── mock_adapter.py
│   └── sitl_adapter.py
│
├── capabilities/
│   ├── altitude_hold/
│   │   ├── module.py
│   │   ├── schema.py
│   │   └── verifier.py
│   │
│   ├── position_hold/
│   └── go_to_target/
│
├── experiments/
│   ├── runner.py
│   └── scenarios/
│
├── reports/
│   ├── generator.py
│   └── templates/
│
├── runs/
│
└── tests/
    ├── unit/
    ├── integration/
    └── scenario/
```

---

# 28. Milestone nên báo cáo leader

| Milestone | Kết quả phải có |
|---|---|
| M0 | Architecture + Module Contract |
| M1 | BaseCapabilityModule chạy được |
| M2 | Mock UAV backend |
| M3 | AltitudeHoldModule chạy end-to-end |
| M4 | Automatic verification |
| M5 | Failure + recovery |
| M6 | Dashboard/report |
| M7 | Test matrix hoàn chỉnh |
| M8 | SITL integration |
| M9 | Repeatability evaluation |
| M10 | Module Contract v1 freeze |
| M11 | PositionHold reuse test |
| M12 | GoToTarget composition |

Sau M12 mới có đủ bằng chứng để nói:

> “Kiến trúc module hóa có khả năng mở rộng.”

---

# 29. Tiêu chí để kết luận giai đoạn đầu thành công

Không phải chỉ:

```text
AltitudeHold = PASS
```

Mà phải đạt đồng thời:

```text
Module có input/output chuẩn.

Module chạy độc lập.

Verifier tách riêng.

PASS/FAIL deterministic.

Failure code chuẩn hóa.

Recovery chạy được.

Artifact được sinh tự động.

Dashboard hiển thị evidence.

Mock và SITL dùng chung module logic.

Module thứ hai có thể tái sử dụng framework.
```

Nếu đạt những điểm đó thì module đầu tiên đã hoàn thành vai trò của nó:

> **Không chỉ giải bài toán giữ độ cao, mà chứng minh kiến trúc cơ sở để phát triển toàn bộ hệ thống capability UAV.**

---

# 30. Chuỗi triển khai thực tế nên làm ngay

```text
Architecture document
        ↓
Module Contract
        ↓
BaseCapabilityModule
        ↓
Request / Result schema
        ↓
Mock UAV Interface
        ↓
AltitudeHold Verifier
        ↓
AltitudeHoldModule
        ↓
ExperimentRunner
        ↓
Artifacts
        ↓
PASS test
        ↓
FAIL test
        ↓
Recovery test
        ↓
HTML Dashboard
        ↓
SITL Adapter
        ↓
Repeat experiments
        ↓
Freeze v1
        ↓
PositionHoldModule
        ↓
GoToTargetModule
```

Đây là hướng triển khai theo nguyên tắc:

> **Module-first, verification-first, simulation-first.**

Mỗi bước đều phải có sản phẩm chạy được, mỗi kết quả đều phải có bằng chứng kiểm chứng, và mọi thành phần đều hướng tới khả năng mở rộng thành một hệ thống UAV capability tổng quát và cuối cùng là AI Agent orchestrator.
