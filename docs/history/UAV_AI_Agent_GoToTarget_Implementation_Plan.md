# KẾ HOẠCH TRIỂN KHAI UAV CAPABILITY FRAMEWORK VỚI AI AGENT

## 1. Mục tiêu của phase đầu

Mục tiêu của phase đầu không phải là xây toàn bộ hệ thống UAV tự chủ, mà là xây và kiểm chứng một **Capability Module gốc** có thể trở thành khuôn mẫu cho toàn bộ các module sau này.

Module được chọn là:

**`GoToTargetModule` — UAV bay từ vị trí hiện tại tới một điểm đích xác định.**

Module này phải chứng minh được toàn bộ chu trình:

```text
Specification
    ↓
AI Agent triển khai
    ↓
Candidate Module
    ↓
Boundary / Code Check
    ↓
PX4 SITL Experiment
    ↓
Telemetry / Evidence
    ↓
Independent Verifier
    ↓
PASS / FAIL
       │
       └── FAIL → Recovery Agent → sửa code → chạy lại
    ↓
Visualization
    ↓
Final Report
```

Definition of Done của proof-of-concept đầu tiên là hệ thống phải chạy được một experiment và trả ra đầy đủ:

```text
Target           (10.0, 5.0, -3.0)
Actual           (10.12, 4.91, -3.06)

Error            0.16 m
Allowed           0.50 m

Execution         PASS
Verification      PASS
Boundary          PASS

FINAL             PASS
```

---

# 2. Nguyên tắc kiến trúc

Toàn bộ framework phải tuân theo chuỗi:

```text
INPUT
 ↓
PROCESS
 ↓
OUTPUT
 ↓
MEASURE
 ↓
VERIFY
 ↓
PASS / FAIL
 ↓
EVIDENCE
```

Các nguyên tắc cốt lõi:

1. Specification phải có trước implementation.
2. Executor không được tự làm Verifier.
3. AI Agent không được sửa tiêu chuẩn đánh giá.
4. Mọi kết luận PASS/FAIL phải truy ngược được về evidence.
5. Visualization chỉ dùng để quan sát, không phải nguồn quyết định PASS/FAIL.
6. Capability phải độc lập với platform thông qua Adapter.
7. Proof-of-concept đầu tiên chỉ chạy trong simulation.
8. Recovery Agent chỉ được sửa implementation trong phạm vi cho phép.
9. Mọi experiment phải reproducible và lưu lại đầy đủ artifact.
10. Module đầu tiên phải trở thành template cho các module tiếp theo.

---

# 3. Kiến trúc tổng thể

```text
                  HUMAN
                    │
          đưa Specification
                    │
                    ▼
                AI AGENT
                    │
               triển khai
                    ▼
           Capability Module
                    │
                    ▼
              UAV Simulator
                    │
                    ▼
                EVIDENCE
                    │
                    ▼
                VERIFIER
              /          \
           PASS          FAIL
                           │
                    Recovery Agent
                           │
                        sửa lại
                           │
                           └────→ chạy lại

                    ↓

            VISUALIZATION + REPORT
```

AI Agent là worker phát triển module, không phải nguồn chân lý.

Nguồn chân lý phải là:

```text
Specification
+
Measured Evidence
+
Verification Rule
```

---

# 4. Module gốc

Framework gồm hai cấp:

```text
BaseCapabilityModule
        │
        └── GoToTargetModule
```

## 4.1 BaseCapabilityModule

`BaseCapabilityModule` định nghĩa lifecycle chuẩn cho mọi Capability Module.

```text
CREATED
   ↓
VALIDATE_INPUT
   ↓
CHECK_PRECONDITIONS
   ↓
EXECUTE
   ↓
COLLECT_RESULT
   ↓
FINISHED
```

Interface khái niệm:

```python
class BaseCapabilityModule:
    validate_input()
    check_preconditions()
    execute()
    build_result()
```

Có thể mở rộng contract chung thành:

```text
validate_input()
check_preconditions()
execute()
collect_evidence()
verify()
recover()
report()
```

Tuy nhiên verifier thực tế phải được tách độc lập khỏi implementation đang được kiểm thử.

---

# 5. Cấu trúc bên trong GoToTargetModule

`GoToTargetModule` vẫn là một capability hoàn chỉnh ở góc nhìn bên ngoài:

```text
Input:
Target

        ↓

GoToTargetModule

        ↓

Output:
Completed / Failed
```

Bên trong chia thành:

```text
GoToTargetModule
│
├── InputValidator
├── PreconditionChecker
├── TargetProcessor
│   └── FrameConverter
├── NavigationExecutor
│   └── UAVAdapter
├── GoalChecker
└── ResultBuilder
```

Các thành phần ngoài module:

```text
External
│
├── TelemetryRecorder
├── GoToTargetVerifier
└── ReportGenerator
```

---

# 6. Input của module

Input phải có schema rõ ràng.

Ví dụ:

```json
{
  "task_id": "NAV-001-T01",
  "frame": "LOCAL_NED",
  "target_position_m": [10.0, 5.0, -3.0],
  "position_tolerance_m": 0.5,
  "timeout_s": 30
}
```

Ý nghĩa:

| Trường | Ý nghĩa |
|---|---|
| `task_id` | ID của testcase |
| `frame` | Hệ tọa độ |
| `target_position_m` | Điểm đích |
| `position_tolerance_m` | Sai số cho phép |
| `timeout_s` | Thời gian tối đa |

Preconditions:

```text
UAV connected       = true
UAV airborne        = true
Localization        = available
Target              = valid
```

Nếu không thỏa precondition, module phải từ chối chạy.

---

# 7. Output của module

Ví dụ:

```json
{
  "task_id": "NAV-001-T01",
  "status": "completed",
  "target_position": [10.0, 5.0, -3.0],
  "final_position": [10.12, 4.91, -3.06],
  "position_error_m": 0.16,
  "elapsed_time_s": 10.4
}
```

Cần phân biệt ba loại output:

```text
MODULE OUTPUT
"Tôi đã hoàn thành"

        ↓

MEASURED OUTPUT
"UAV thực tế ở đâu?"

        ↓

VERIFIED RESULT
"Sai số bao nhiêu? PASS hay FAIL?"
```

Module Output không phải kết quả chính thức.

---

# 8. Specification NAV-001

File:

```text
specs/NAV-001.yaml
```

Nội dung:

```text
Module:
GoToTargetModule

Capability:
navigate_to_target

Input:
- target position
- coordinate frame
- tolerance
- timeout

Preconditions:
- connected
- airborne
- localization valid

Output:
- execution status
- final position
- elapsed time

Acceptance:
- position_error <= tolerance
- elapsed <= timeout
```

Specification phải được đóng băng trước khi Agent thực hiện implementation.

Agent không được sửa:

```text
specs/
tests/acceptance/
verification/
```

---

# 9. Repository đề xuất

```text
uav-capability-framework/
│
├── specs/
│   └── NAV-001.yaml
│
├── src/
│   ├── core/
│   │   ├── base_capability.py
│   │   ├── result.py
│   │   └── registry.py
│   │
│   ├── capabilities/
│   │   └── navigation/
│   │       └── go_to_target.py
│   │
│   ├── adapters/
│   │   ├── base_uav_adapter.py
│   │   └── px4_sitl_adapter.py
│   │
│   ├── telemetry/
│   │   └── recorder.py
│   │
│   └── verification/
│       └── goto_target_verifier.py
│
├── agents/
│   ├── implementation/
│   ├── test/
│   ├── execution/
│   └── recovery/
│
├── tests/
│   ├── unit/
│   └── acceptance/
│
├── scenarios/
│
├── runs/
│
├── reports/
│
└── visualization/
```

---

# 10. Phase 0 — Khóa specification

## Task 0.1 — Viết NAV-001

Phải định nghĩa rõ:

```text
Input
Output
Precondition
Acceptance Criteria
Failure Condition
Boundary
Evidence
```

## Task 0.2 — Khóa acceptance criteria

Ví dụ:

```text
position_error <= 0.5 m
elapsed <= 30 s
localization_valid = true
telemetry_available = true
```

## Task 0.3 — Định nghĩa Agent Boundary

Agent được sửa:

```text
src/capabilities/navigation/
```

Agent có thể sửa giới hạn:

```text
src/core/
```

Agent không được sửa:

```text
specs/
tests/acceptance/
verification/
run artifacts cũ
```

## Gate Phase 0

```text
Specification complete       PASS
Input schema defined         PASS
Output schema defined        PASS
Acceptance frozen            PASS
Agent boundaries defined     PASS
```

---

# 11. Phase 1 — Xây skeleton project

Tạo repository theo cấu trúc chuẩn.

Mục tiêu là đảm bảo các module sau này không cần thay đổi kiến trúc.

Các thành phần đầu tiên:

```text
BaseCapabilityModule
CapabilityResult
CapabilityRegistry
UAVAdapter
TelemetryRecorder
Verifier Interface
Experiment Runner
```

---

# 12. Phase 2 — Xây simulator baseline

Giai đoạn này chưa dùng AI Agent.

Mục tiêu:

```text
Python/Application
      ↓
PX4 SITL
      ↓
Simulated UAV
      ↓
Telemetry
```

Phải chứng minh:

```text
Start simulator     PASS
Vehicle available   PASS
Read position       PASS
Telemetry stream    PASS
Stop cleanly        PASS
```

Có hai môi trường:

## 12.1 Automated environment

Dùng SITL ở chế độ phù hợp cho automated test.

```text
Agent
 ↓
Automated Test
 ↓
PX4 SITL
 ↓
Telemetry
```

Mục tiêu:

```text
Nhanh
Reproducible
Có thể chạy nhiều testcase
```

## 12.2 Visual environment

Dùng:

```text
PX4 SITL
+
Gazebo
```

Mục đích:

```text
Quan sát 3D
Demo
Replay
So sánh attempt
```

---

# 13. Phase 3 — Xây UAVAdapter

Kiến trúc:

```text
Capability
    ↓
UAVAdapter
    ↓
PX4
```

Interface khái niệm:

```python
class UAVAdapter:
    def connect(...)
    def get_state(...)
    def get_position(...)
    def send_position_target(...)
    def abort(...)
```

`GoToTargetModule` không được phụ thuộc trực tiếp vào PX4 internals.

Sau này:

```text
GoToTargetModule
      │
      ├── PX4SITLAdapter
      ├── HardwareAdapter
      └── MockAdapter
```

## Gate Phase 3

```text
Connect               PASS
Read state            PASS
Read position         PASS
Submit target         PASS
Abort                 PASS
```

---

# 14. Phase 4 — Xây hệ thống Evidence

Mỗi lần experiment tạo:

```text
runs/
└── NAV-001/
    └── T01/
        └── attempt_001/
            ├── input.json
            ├── telemetry.csv
            ├── events.jsonl
            ├── module_output.json
            ├── execution.log
            └── metadata.json
```

Telemetry tối thiểu:

```text
timestamp
x
y
z
vx
vy
vz
vehicle_state
localization_valid
```

Nguyên tắc:

```text
Module says SUCCESS
        ≠
Experiment PASS
```

Chỉ:

```text
Evidence
   ↓
Verifier
   ↓
PASS
```

mới được công nhận.

---

# 15. Phase 5 — Xây Independent Verifier

Verifier đọc:

```text
input.json
+
telemetry.csv
```

Không dùng `module_output.status` để quyết định PASS.

## 15.1 Kiểm tra position

Target:

\[
P_t=(x_t,y_t,z_t)
\]

Measured final position:

\[
P_f=(x_f,y_f,z_f)
\]

Error:

\[
e=\sqrt{(x_f-x_t)^2+(y_f-y_t)^2+(z_f-z_t)^2}
\]

PASS khi:

\[
e\leq tolerance
\]

Ví dụ:

```text
Target        (10.00, 5.00, -3.00)
Final         (10.12, 4.91, -3.06)

3D error      0.16 m
Requirement   <= 0.50 m

Position      PASS
```

## 15.2 Kiểm tra thời gian

```text
elapsed <= timeout
```

## 15.3 Kiểm tra evidence

```text
valid telemetry
valid timestamp
valid localization
```

Output:

```json
{
  "task": "NAV-001-T01",
  "checks": {
    "position": "PASS",
    "timeout": "PASS",
    "telemetry": "PASS"
  },
  "position_error_m": 0.16,
  "overall": "PASS"
}
```

---

# 16. Phase 6 — Implement BaseCapabilityModule

Lifecycle:

```text
CREATED
   ↓
VALIDATE_INPUT
   ↓
CHECK_PRECONDITIONS
   ↓
EXECUTE
   ↓
COLLECT_RESULT
   ↓
FINISHED
```

Tạo các thành phần chung:

```text
Input validation
Precondition handling
Timeout handling
Result model
Error model
Lifecycle state
Logging interface
```

---

# 17. Phase 7 — Implement GoToTargetModule

Runtime:

```text
Input
 ↓
Validate
 ↓
Check Preconditions
 ↓
Normalize Target / Frame
 ↓
Send Target
 ↓
Monitor State
 ↓
Goal Condition?
 ↓
Complete / Timeout
 ↓
Module Output
```

Các thành phần:

```text
InputValidator
PreconditionChecker
TargetProcessor
FrameConverter
NavigationExecutor
GoalChecker
ResultBuilder
```

Goal Checker dùng trong runtime để xác định khi nào dừng execution.

Goal Checker không phải verifier chính thức.

---

# 18. Phase 8 — Baseline thủ công

Trước khi giao Agent tự triển khai, cần có Known Good Baseline.

Ví dụ:

```text
NAV-001-T01

Start:
(0,0,-2)

Target:
(10,5,-3)

Tolerance:
0.5 m
```

Result:

```text
Final:
(10.12,4.91,-3.06)

Error:
0.16 m

PASS
```

Baseline phải chứng minh:

```text
Simulator works
Adapter works
Telemetry works
Verifier works
```

Nếu baseline chưa ổn định thì chưa được đưa Agent vào.

---

# 19. Phase 9 — Đưa Implementation Agent vào

Agent nhận:

```text
NAV-001 Specification
+
BaseCapability API
+
UAVAdapter API
+
repository context được phép
```

Yêu cầu:

```text
Implement GoToTargetModule.
Không sửa specification.
Không sửa acceptance tests.
Không sửa verifier.
```

Pipeline:

```text
Implementation Agent
       ↓
Candidate Patch
       ↓
Provider Check
       ↓
Boundary Check
       ↓
Unit Test
       ↓
SITL Experiment
       ↓
Verifier
```

Nếu PASS:

```text
Candidate Accepted
```

Nếu FAIL:

```text
Send to Recovery Agent
```

---

# 20. Vai trò của các AI Agent

Có thể dùng các role:

```text
Orchestrator Agent
│
├── Spec Agent
├── Implementation Agent
├── Test Agent
├── Execution Agent
├── Verifier Agent
└── Recovery Agent
```

Không bắt buộc dùng nhiều model.

Ban đầu có thể là các role/workflow riêng của cùng một model.

## Spec Agent

Nhiệm vụ:

```text
Từ requirement tạo specification.
```

Trong experiment thực tế, spec phải được freeze trước khi implementation bắt đầu.

## Implementation Agent

Nhiệm vụ:

```text
Sinh / chỉnh sửa implementation.
```

## Test Agent

Nhiệm vụ:

```text
Sinh testcase nhưng không được tự sửa acceptance criteria.
```

## Execution Agent

Nhiệm vụ:

```text
Prepare environment
Run experiment
Collect artifacts
```

Không quyết định PASS/FAIL.

## Verifier Agent

Đọc output của Independent Verifier, tổng hợp và giải thích.

Không thay thế verifier toán học.

## Recovery Agent

Nhận:

```text
Source code
Failure reason
Telemetry
Logs
```

và tạo patch mới.

---

# 21. Phase 10 — Recovery Experiment

Đây là experiment chính để chứng minh giá trị của Agent.

Cố tình tạo bug trong implementation.

Ví dụ Goal Checker chỉ tính XY:

\[
\sqrt{dx^2+dy^2}
\]

nhưng bỏ Z.

Scenario:

```text
Target:
(10,5,-5)

Actual:
(10,5,-3)
```

Implementation sai:

```text
XY error = 0
SUCCESS
```

Verifier độc lập:

```text
3D error = 2 m
FAIL
```

Kết quả attempt 1:

```text
ATTEMPT 1

Provider       PASS
Boundary       PASS
Execution      PASS
Verification   FAIL
```

Recovery Agent nhận:

```text
Source code
Failure report
Telemetry
Execution log
```

Agent được phép sửa implementation.

Không được sửa:

```text
Spec
Verifier
Acceptance Test
```

Sau khi patch:

```text
ATTEMPT 2

Provider       PASS
Boundary       PASS
Execution      PASS
Verification   PASS
```

Cuối cùng:

```text
Candidate Accepted
```

Chuỗi được chứng minh:

```text
Agent sinh code
      ↓
Code có lỗi
      ↓
Verifier phát hiện
      ↓
Agent sửa
      ↓
Verifier xác nhận
```

---

# 22. Phase 11 — Visualization V1

Không xây dashboard realtime ngay.

Trước tiên tạo offline visualization.

Sau mỗi run:

```text
trajectory.png
position_error.png
verification_summary.png
report.md
```

## 22.1 Trajectory

Show:

```text
START ●─────────────→ X TARGET
          actual
         trajectory
```

Cần có:

```text
Start marker
Target marker
Actual trajectory
Final position
```

## 22.2 Error theo thời gian

Ví dụ:

```text
Position Error

12m │●
    │ \
 8m │  \
    │   \
 4m │    \
    │      \
0.5m│---------- Acceptance threshold
    │         \
0m  │          ●
    └──────────────────→ time
```

Mục tiêu:

```text
Ban đầu error lớn
↓
UAV tiến tới target
↓
Error giảm
↓
Error nhỏ hơn threshold
```

---

# 23. Phase 12 — Visualization V2: Live Dashboard

Khi pipeline đã ổn định mới xây live dashboard.

Giao diện đề xuất:

```text
┌─────────────────────────────────────────────┐
│ NAV-001 — ATTEMPT 2                        │
├──────────────────────┬──────────────────────┤
│                      │ INPUT                │
│                      │                      │
│   3D SIMULATION      │ Target  10,5,-3     │
│                      │ Tol     0.5 m        │
│      UAV ●───→ X     │ Timeout 30 s        │
│                      │                      │
│                      │ STATE                │
│                      │ Error   1.43 m       │
│                      │ Time    7.4 s        │
├──────────────────────┴──────────────────────┤
│ ERROR                                       │
│                                             │
│   \                                         │
│    \______                                  │
│ ---------- threshold                        │
├─────────────────────────────────────────────┤
│ VERIFICATION                                │
│ Position                    PASS             │
│ Timeout                     PASS             │
│ Telemetry                   PASS             │
│                                             │
│ OVERALL                     PASS             │
└─────────────────────────────────────────────┘
```

Dashboard chỉ đọc telemetry/evidence.

Không được phép ảnh hưởng kết quả verifier.

---

# 24. Phase 13 — Replay và so sánh attempt

Sau Recovery Agent, tạo khả năng replay.

Ví dụ:

```text
ATTEMPT 1                 ATTEMPT 2

Trajectory                Trajectory

       ●                         X
      /                        /
     /                        ●
    X

Error: 0.83 m             Error: 0.18 m

FAIL                      PASS
```

Kèm thông tin:

```text
Failure reason:
Goal calculation incorrect.

Recovery:
Implementation patched.

Specification changed:
NO

Verifier changed:
NO
```

Đây là visual proof cho thấy Agent đã sửa implementation thực sự.

---

# 25. Phase 14 — Test Suite

Không dùng duy nhất một testcase.

Bộ test ban đầu:

| ID | Scenario | Expected |
|---|---|---|
| T01 | Target thông thường | PASS |
| T02 | Thay đổi cả X/Y/Z | PASS |
| T03 | Target rất gần | PASS |
| T04 | Input thiếu thành phần | REJECT |
| T05 | Frame không hỗ trợ | REJECT |
| T06 | Localization invalid | PRECONDITION FAIL |
| T07 | Timeout | FAIL |
| T08 | Module trả success sai | Verifier bắt FAIL |

Module đáng tin phải biết cả:

```text
Khi nào được chạy
```

và:

```text
Khi nào phải từ chối chạy
```

---

# 26. Phase 15 — Experiment Matrix

Không tuyên bố module đúng chỉ vì một lần PASS.

Chạy nhiều lần:

```text
T01 × 10
T02 × 10
T03 × 10
...
```

Report thống kê:

```text
Total runs:        70
Passed:            68
Failed:             2

Success rate:      97.1 %

Mean error:        0.19 m
Max error:         0.47 m
Mean duration:     9.8 s
```

Mục tiêu là xây evidence về độ tin cậy của capability.

---

# 27. Phase 16 — Report tự động

Mỗi experiment sinh:

```text
result.json
verification.json
telemetry.csv
trajectory.png
position_error.png
agent_attempts.json
report.md
```

Report cuối cần show rõ ba nhóm:

## REQUESTED

```text
Target      (10,5,-3)
Tolerance   0.5 m
Timeout     30 s
```

## OBSERVED

```text
Final       (10.12,4.91,-3.06)
Error       0.16 m
Time        10.4 s
```

## VERIFIED

```text
Position    PASS
Timeout     PASS
Evidence    PASS

OVERALL     PASS
```

---

# 28. Cơ sở kiểm chứng

Không dựa vào việc Agent nói kết quả đúng.

Kết quả phải dựa trên:

```text
Specification
+
Measurement Source
+
Verification Formula
```

Ví dụ:

```text
Specification:
error <= 0.5 m

Measurement:
position từ telemetry

Formula:
Euclidean 3D distance
```

Do đó:

```text
Objective Verification
```

được hình thành.

---

# 29. Nguồn Evidence

Trong simulation:

```text
Simulator / Autopilot
        ↓
Telemetry
        ↓
Telemetry Recorder
        ↓
Independent Verifier
```

Agent không tự cung cấp final position để làm bằng chứng.

Nếu module trả:

```text
SUCCESS
```

nhưng telemetry cho:

```text
Error = 1.4 m
```

thì kết quả chính thức vẫn là:

```text
FAIL
```

---

# 30. Boundary Policy

Ví dụ:

| Vùng | Agent được sửa |
|---|---|
| `src/capabilities/navigation/` | Có |
| `src/core/` | Có giới hạn |
| `specs/` | Không |
| `tests/acceptance/` | Không |
| `verification/` | Không |
| run artifacts cũ | Không |

Nếu Agent cố sửa:

```text
Tolerance 0.5 m → 1.0 m
```

để làm test PASS:

```text
BOUNDARY FAIL
```

---

# 31. Các failure case cần quản lý

Module phải có failure model.

Ví dụ:

```text
INVALID_INPUT
PRECONDITION_FAILED
LOCALIZATION_LOST
TIMEOUT
EXECUTION_ERROR
TARGET_NOT_REACHED
VERIFICATION_FAILED
```

Mỗi failure phải:

```text
Có mã lỗi
Có evidence
Có nguyên nhân
Có log
Có recovery policy
```

---

# 32. Những điểm kỹ thuật đặc biệt cần chú ý

## 32.1 Coordinate Frame

Không dùng:

```text
target = [10,5,3]
```

mà không biết frame.

Phải luôn có:

```text
frame
x
y
z
```

Ví dụ:

```text
LOCAL_NED
```

Frame Converter nên là internal component tái sử dụng.

---

## 32.2 Executor và Verifier phải độc lập

Không:

```text
GoToTargetModule
→ execute
→ đo
→ tự kết luận PASS
```

Mà:

```text
GoToTargetModule
        ↓
Execution Result

Telemetry
        ↓
Independent Verifier
        ↓
PASS / FAIL
```

---

## 32.3 Telemetry Recorder phải độc lập

Kiến trúc:

```text
               UAV
              /   \
             /     \
Navigation Executor Telemetry Recorder
```

Nếu executor sai, telemetry vẫn còn để kiểm tra.

---

## 32.4 Không để AI Agent điều khiển actuator trực tiếp

Agent chỉ hoạt động ở tầng:

```text
Capability
Planning
Implementation
Testing
Recovery
```

Không sinh trực tiếp lệnh motor realtime.

Flight-control loop thuộc autopilot/controller.

---

## 32.5 Visualization không thay thế verifier

```text
Visualization:
Human understanding

Verifier:
Machine acceptance
```

Hai hệ thống nên đọc cùng một nguồn evidence:

```text
                  Telemetry
                 /         \
                ↓           ↓
          Visualization   Verifier
```

---

# 33. Màn hình demo cuối

```text
================================================
           UAV CAPABILITY TEST
================================================

Module:
NAV-001 GoToTarget

INPUT
-----------------------------------------------
Start             (0.00, 0.00, -2.00)
Target            (10.00, 5.00, -3.00)
Tolerance         0.50 m
Timeout           30 s

EXECUTION
-----------------------------------------------
Start time        14:32:01
End time          14:32:11
Elapsed           10.4 s

MEASURED RESULT
-----------------------------------------------
Final X           10.12
Final Y           4.91
Final Z           -3.06

Position Error    0.16 m

VERIFICATION
-----------------------------------------------
Error <= 0.50 m          PASS
Timeout <= 30 s          PASS
Localization valid       PASS
Telemetry available      PASS

FINAL RESULT
-----------------------------------------------

                 PASS

================================================
```

Bên cạnh bảng này cần show:

```text
3D Simulator
Trajectory Graph
Position Error Graph
Attempt / Recovery history
```

---

# 34. Definition of Done cho GoToTargetModule v1.0

Module chỉ được coi là hoàn thiện khi tất cả điều kiện sau PASS.

| Nhóm | Điều kiện |
|---|---|
| Specification | PASS |
| Input validation | PASS |
| Preconditions | PASS |
| UAV Adapter | PASS |
| SITL execution | PASS |
| Telemetry recording | PASS |
| Independent verification | PASS |
| Acceptance tests | PASS |
| Agent implementation | PASS |
| Boundary protection | PASS |
| Failure detection | PASS |
| Agent recovery | PASS |
| Re-execution | PASS |
| Visualization | PASS |
| Evidence report | PASS |
| Repeatability | PASS |

---

# 35. Capability Template sau khi hoàn thiện

Sau khi NAV-001 ổn định, rút ra `CapabilityTemplate`.

Template gồm:

```text
Specification Template
Input Schema
Base Class
Adapter Interface
Telemetry Interface
Verifier Interface
Test Structure
Agent Workflow
Artifact Structure
Visualization
Report
```

Mỗi module mới chỉ phải định nghĩa phần riêng.

---

# 36. Module tiếp theo

Thứ tự khuyến nghị:

```text
1. GoToTargetModule
2. TakeoffModule
3. LandModule
4. AltitudeHoldModule
5. PositionHoldModule
6. FollowTrajectoryModule
```

Sau khi có:

```text
TakeoffModule
GoToTargetModule
LandModule
```

có thể tạo mission:

```text
Takeoff
    ↓
GoToTarget
    ↓
Land
```

Sau đó phát triển:

```text
MissionExecutionModule
```

để compose nhiều capability.

---

# 37. Hướng mở rộng theo level

Framework hiện tại sẽ phục vụ các Level cao hơn.

```text
Level 0
Command Execution
        ↓
Level 1
State Stabilization
        ↓
Level 2
Target Navigation
        ↓
Level 3
Mission Execution
        ↓
Level 4
Adaptive Mission
        ↓
Level 5
Goal-driven Autonomy
```

Module Level thấp được tái sử dụng ở Level cao.

Ví dụ:

```text
Level 3
=
TakeoffModule
+
GoToTargetModule
+
LandModule
+
MissionExecutionModule
```

Level 4 bổ sung:

```text
EventDetector
ReplanningModule
RecoveryModule
```

Level 5 bổ sung:

```text
GoalInterpreter
RequirementModule
TaskDecomposer
CapabilityRouter
Orchestrator
```

---

# 38. Giá trị của PoC đầu tiên

Nếu hoàn thành chu trình:

```text
Specification
    ↓
AI Agent implementation
    ↓
Candidate Module
    ↓
Simulation
    ↓
Evidence
    ↓
Independent Verification
    ↓
FAIL
    ↓
Recovery Agent
    ↓
Patch
    ↓
Run again
    ↓
PASS
    ↓
Visualization + Report
```

thì PoC đã chứng minh ba ý tưởng quan trọng:

1. Capability Module architecture triển khai được.
2. Module có thể được kiểm chứng độc lập bằng evidence thực nghiệm.
3. AI Agent có thể tự động triển khai, kiểm thử, phát hiện lỗi, sửa và được chấp nhận chỉ khi verifier PASS.

---

# 39. Kết luận triển khai

Mốc quan trọng nhất của phase đầu **không phải chỉ là UAV bay được trong simulator**.

Mốc cần chứng minh là:

> AI Agent có thể tự triển khai một UAV capability dựa trên specification cố định, hệ thống độc lập thu evidence, verifier đánh giá PASS/FAIL, phát hiện implementation sai, Recovery Agent sửa lại implementation mà không được sửa tiêu chuẩn đánh giá, sau đó hệ thống chạy lại và chỉ chấp nhận khi toàn bộ acceptance criteria PASS.

Khi `GoToTargetModule` đạt trạng thái đó, nó trở thành **reference implementation** cho toàn bộ UAV Capability Framework.

Các module tiếp theo sẽ không cần phát minh lại kiến trúc mà chỉ kế thừa chu trình:

```text
SPEC
 ↓
IMPLEMENT
 ↓
EXECUTE
 ↓
MEASURE
 ↓
VERIFY
 ↓
RECOVER
 ↓
REPORT
```

Đây là nền tảng để mở rộng dần từ capability nhỏ sang Mission Execution, Adaptive Mission và cuối cùng là Goal-driven UAV Autonomy được điều phối bởi AI Agent.
