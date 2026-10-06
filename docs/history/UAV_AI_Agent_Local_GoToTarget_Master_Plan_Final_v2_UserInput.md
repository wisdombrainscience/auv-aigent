# KẾ HOẠCH TRIỂN KHAI HOÀN CHỈNH
# UAV CAPABILITY FRAMEWORK VỚI AI AGENT LOCAL

> Phiên bản hoàn thiện — tập trung vào PoC `GoToTargetModule`, sử dụng **OpenCode + Ollama + một Local LLM** cho nhiều Agent Role độc lập; **Python deterministic** cho orchestration, experiment và verification; **PX4 SITL + Gazebo** cho mô phỏng và kiểm nghiệm trực quan.

---

# 1. Mục tiêu của dự án ở phase hiện tại

Mục tiêu hiện tại **không phải** xây ngay toàn bộ UAV tự chủ hoặc hệ Multi-Agent phức tạp.

Mục tiêu là xây một **Capability Framework chuẩn**, sau đó lấy một capability nhỏ nhưng đủ đại diện để kiểm chứng toàn bộ kiến trúc.

Capability được chọn:

```text
GoToTargetModule
```

Ý nghĩa:

> UAV đang ở một vị trí hiện tại. Hệ thống nhận một target `(x, y, z)` và phải làm cho UAV đi tới target đó trong sai số cho phép.

PoC phải chứng minh được đầy đủ chu trình:

```text
Frozen Specification
        ↓
AI Implementation Job
        ↓
Candidate Module
        ↓
Boundary Check
        ↓
Unit / Static Check
        ↓
PX4 SITL Experiment
        ↓
Telemetry / Evidence
        ↓
Independent Deterministic Verifier
        ↓
PASS / FAIL
      /        \
   PASS        FAIL
    │            │
 ACCEPT      Failure Package
                 ↓
          AI Recovery Job
                 ↓
              Patch
                 ↓
              Rerun
                 ↓
            PASS / FAIL
                 ↓
        Visualization + Report
```

Mục tiêu cuối của PoC:

> Chứng minh rằng AI có thể tự triển khai và sửa một UAV capability theo specification cố định, nhưng AI **không được quyền tự chấm kết quả**. Candidate chỉ được chấp nhận khi deterministic verifier xác nhận PASS bằng evidence thực nghiệm.

---

# 2. Quyết định kiến trúc quan trọng

Toàn bộ framework tuân theo chuỗi:

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

Các nguyên tắc bắt buộc:

1. Specification phải tồn tại trước implementation.
2. Acceptance criteria phải được freeze trước khi AI bắt đầu sửa code.
3. Executor không được tự làm verifier.
4. AI không được sửa verifier để làm test PASS.
5. AI không được sửa acceptance criteria.
6. Mọi PASS/FAIL phải truy ngược được về evidence.
7. Capability phải độc lập với simulator/platform thông qua Adapter.
8. Visualization chỉ hỗ trợ con người quan sát, không quyết định PASS/FAIL.
9. Experiment phải reproducible.
10. Mọi Agent Job phải có log, changed-file manifest và boundary check.
11. PoC đầu tiên chỉ chạy trong simulation.
12. Chỉ dùng AI ở nơi cần reasoning/coding; các bước deterministic phải giữ deterministic.
13. Một local model có thể đảm nhiệm nhiều Agent Role, nhưng các role phải chạy dưới **job/session độc lập**.
14. Không dùng một session AI dài để vừa code, vừa tự test, vừa tự chấm, vừa tự sửa.
15. Module đầu tiên phải trở thành reference template cho các module UAV sau.

---

# 3. Kiến trúc AI được chốt

## 3.1. Thành phần

```text
OpenCode     = Agent Runner / coding agent interface
Ollama       = Local model runtime
Local LLM    = Model AI thực hiện coding/reasoning
Python       = Orchestrator + Runner + Verifier
PX4 SITL     = Flight stack mô phỏng
Gazebo       = Môi trường mô phỏng 3D
```

Baseline Local LLM:

```text
qwen2.5-coder:3b
```

Model này chỉ là baseline để kiểm chứng architecture.

Framework không được hard-code model.

Ví dụ:

```yaml
provider: ollama
model: qwen2.5-coder:3b
```

Sau này có thể đổi model mà không thay đổi:

```text
Specification
Acceptance Criteria
Evidence Contract
Verifier
Experiment Scenarios
```

---

# 3.2. Một AI hay nhiều AI?

Ở phase đầu:

```text
ONE LOCAL MODEL
      │
    OpenCode
      │
 ┌────┼───────────────┐
 ↓    ↓               ↓
Implementation    Test Generation    Recovery
Role              Role               Role
```

Tức là:

> Cùng một model AI có thể làm nhiều chức năng khác nhau, nhưng mỗi chức năng được chạy như một **Agent Job riêng**.

Ví dụ:

```text
JOB #1
Implementation Role
→ session riêng
→ context riêng
→ quyền riêng
→ output riêng
→ session kết thúc

JOB #2
Test Generation Role
→ session riêng
→ context riêng
→ quyền riêng
→ output riêng
→ session kết thúc

JOB #3
Recovery Role
→ session riêng
→ failure package riêng
→ quyền riêng
→ patch riêng
→ session kết thúc
```

Không dùng:

```text
Một session duy nhất
→ viết code
→ tự test
→ tự kết luận đúng
→ tự sửa
```

---

# 3.3. Phân chia chức năng AI và non-AI

| Chức năng | Dùng AI? | Công nghệ |
|---|---:|---|
| Đọc spec và viết implementation | Có | OpenCode + Local LLM |
| Phân tích failure | Có | OpenCode + Local LLM |
| Sinh recovery patch | Có | OpenCode + Local LLM |
| Đề xuất testcase | Có | OpenCode + Local LLM |
| Viết giải thích report | Tùy chọn | Local LLM |
| Orchestrate workflow | Không | Python |
| Start/stop simulator | Không | Python |
| Chạy experiment | Không | Python |
| Ghi telemetry | Không | Python |
| Tính metric | Không | Python |
| PASS / FAIL | Không | Python deterministic |
| Physics / flight dynamics | Không | PX4 SITL + Gazebo |
| Visualization | Không bắt buộc AI | Python/dashboard |

Nguyên tắc:

```text
Nếu deterministic logic giải quyết tốt hơn
→ không dùng AI.
```

---

# 4. Vai trò của từng Agent Role

## 4.1. Implementation Role

Công nghệ:

```text
OpenCode
+
Ollama
+
Configured Local Coding Model
```

Input:

```text
NAV-001 specification
BaseCapabilityModule API
UAVAdapter API
Allowed repository context
Coding conventions
Boundary policy
```

Nhiệm vụ:

```text
Implement GoToTargetModule
```

Output bắt buộc:

```text
Candidate patch
Changed-file manifest
Implementation summary
Commands requested/executed
Agent metadata
```

Không được:

```text
Sửa specification
Sửa independent verifier
Sửa frozen acceptance test
Sửa previous evidence
```

---

## 4.2. Test Generation Role

Input:

```text
Frozen specification
Input/output schema
Known failure modes
```

Nhiệm vụ:

```text
Đề xuất testcase
Đề xuất edge case
Sinh unit-test candidate
```

Lưu ý:

> Test do AI sinh chưa tự động trở thành acceptance test.

Quy trình:

```text
AI proposes test
      ↓
Human/framework review
      ↓
Freeze accepted test
      ↓
Recovery Agent không được sửa
```

---

## 4.3. Recovery Role

Được gọi khi deterministic verifier trả:

```text
FAIL
```

Input:

```text
Current implementation
failure.json
Telemetry summary
Execution log
Unit-test output
Boundary policy
```

Nhiệm vụ:

```text
Phân tích nguyên nhân
Tạo minimal patch
Giải thích failure hypothesis
Trả changed-file manifest
```

Không được:

```text
Sửa specification
Sửa verifier
Sửa acceptance threshold
Sửa previous evidence
```

---

## 4.4. Verifier

Verifier **không phải AI Agent**.

Verifier là:

```text
Python deterministic code
```

Nhiệm vụ:

```text
Read evidence
Calculate metric
Compare with frozen acceptance criteria
Return PASS / FAIL
```

AI không có quyền override verdict.

---

## 4.5. Orchestrator

Phase đầu **không dùng LLM làm Orchestrator**.

Dùng:

```text
Python State Machine
```

Logic:

```text
LOAD_SPEC
   ↓
CALL_IMPLEMENTATION_JOB
   ↓
BOUNDARY_CHECK
   ↓
UNIT_TEST
   ↓
RUN_EXPERIMENT
   ↓
VERIFY
   ↓
PASS?
 /   \
YES   NO
 │     │
ACCEPT BUILD_FAILURE_PACKAGE
       ↓
CALL_RECOVERY_JOB
       ↓
BOUNDARY_CHECK
       ↓
RERUN
```

Mục tiêu là giữ workflow dễ debug và reproducible.

---

# 5. Module gốc của Framework

Framework gồm:

```text
BaseCapabilityModule
        │
        └── GoToTargetModule
```

## 5.1. BaseCapabilityModule

Định nghĩa lifecycle chung:

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
    def validate_input(...)
    def check_preconditions(...)
    def execute(...)
    def build_result(...)
```

Có thể mở rộng framework-level contract bằng:

```text
collect_evidence()
report()
```

Nhưng verification chính thức phải nằm ngoài implementation.

---

# 6. Cấu trúc GoToTargetModule

Ở góc nhìn bên ngoài:

```text
Input:
Target

    ↓

GoToTargetModule

    ↓

Output:
Completed / Failed
```

Bên trong:

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

Bên ngoài module:

```text
External
│
├── TelemetryRecorder
├── GoToTargetVerifier
├── ExperimentRunner
└── ReportGenerator
```

---

# 7. Input của module

Schema ví dụ:

```json
{
  "task_id": "NAV-001-T01",
  "frame": "LOCAL_NED",
  "target_position_m": [10.0, 5.0, -3.0],
  "position_tolerance_m": 0.5,
  "timeout_s": 30
}
```

Preconditions:

```text
UAV connected       = true
UAV airborne        = true
Localization valid  = true
Target valid        = true
```

Nếu precondition không đạt:

```text
PRECONDITION_FAILED
```

Module không được cố chạy.

---


# 7.1. User Runtime Input Interface

Framework phải cho phép người dùng **tự nhập thông số cho từng experiment** mà không cần sửa source code.

Phải tách rõ:

```text
FROZEN SPECIFICATION
→ quy định module phải làm gì và đúng khi nào

USER RUNTIME INPUT
→ quy định testcase cụ thể lần này
```

Người dùng được phép thay đổi các tham số runtime đã được specification cho phép.

## 7.1.1. Basic User Input

Các tham số cơ bản:

```text
target_x
target_y
target_z
position_tolerance_m
timeout_s
```

Ví dụ:

```json
{
  "target_position_m": [15.0, -4.0, -6.0],
  "position_tolerance_m": 0.3,
  "timeout_s": 25
}
```

## 7.1.2. Advanced User Input

Có thể mở rộng:

```text
coordinate_frame
speed_limit
altitude_limit
scenario_id
```

Chỉ được cho nhập các field đã được specification cho phép.

## 7.1.3. System Input

Các dữ liệu này **không để người dùng tự khai báo thay cho trạng thái thật**:

```text
current_position
vehicle_state
airborne_state
localization_valid
connection_state
```

Phải đọc tự động từ PX4/telemetry.

## 7.1.4. User Input Processing Flow

```text
User Parameters
      ↓
UserInputInterface
      ↓
Schema Validation
      ↓
Semantic Validation
      ↓
Generate input.json
      ↓
Experiment Runner
      ↓
GoToTargetModule
      ↓
PX4 SITL
      ↓
Telemetry
      ↓
Verifier
      ↓
Result
```

## 7.1.5. CLI phiên bản đầu

PoC đầu tiên nên triển khai CLI trước.

Ví dụ:

```text
Target X: 15
Target Y: -4
Target Z: -6
Tolerance: 0.3
Timeout: 25

[RUN EXPERIMENT]
```

Hoặc command:

```bash
python run_experiment.py \
  --target-x 15 \
  --target-y -4 \
  --target-z -6 \
  --tolerance 0.3 \
  --timeout 25
```

CLI phải tự động tạo:

```text
runs/<task>/<test>/<attempt>/input.json
```

## 7.1.6. UI phiên bản sau

Sau khi CLI ổn định mới xây UI/dashboard.

Ví dụ:

```text
Target X      [ 15.0 ]
Target Y      [ -4.0 ]
Target Z      [ -6.0 ]

Tolerance     [ 0.3 ]
Timeout       [ 25 ]

[ RUN EXPERIMENT ]
```

UI chỉ là lớp nhập liệu.

UI không được sửa:

```text
Frozen specification
Verifier
Acceptance logic
```

## 7.1.7. Validation Rules

User input phải kiểm tra ít nhất:

```text
Required fields exist
Numeric type valid
Finite values
Supported coordinate frame
Tolerance > 0
Timeout > 0
Target inside permitted test region
```

Input invalid phải trả:

```text
INVALID_INPUT
```

và không khởi động experiment.

## 7.1.8. Ví dụ kết quả

Input do user nhập:

```text
Target:
(15.0, -4.0, -6.0)

Tolerance:
0.30 m

Timeout:
25 s
```

Measured:

```text
Final:
(14.87, -4.09, -5.94)
```

Verifier:

```text
Error:
0.17 m

Allowed:
0.30 m

Result:
PASS
```

Nguyên tắc:

> Người dùng được quyền thay đổi **test scenario**, nhưng không được thay đổi tiêu chuẩn nền tảng của module ngoài những parameter mà frozen specification cho phép.

---

# 8. Output của module

Ví dụ:

```json
{
  "task_id": "NAV-001-T01",
  "status": "completed",
  "target_position": [10.0, 5.0, -3.0],
  "final_position": [10.12, 4.91, -3.06],
  "elapsed_time_s": 10.4
}
```

Phải phân biệt:

```text
MODULE OUTPUT
"Tôi đã hoàn thành"

        ↓

MEASURED OUTPUT
"Telemetry nói UAV thực sự ở đâu?"

        ↓

VERIFIED RESULT
"Sai số thực tế có đạt requirement không?"
```

Chỉ `VERIFIED RESULT` mới là kết quả chính thức.

---

# 9. Specification NAV-001

File:

```text
specs/NAV-001.yaml
```

Nội dung tối thiểu:

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
- telemetry valid
```

Sau khi bắt đầu experiment:

```text
NAV-001.yaml = frozen
```

---

# 10. Failure Model

Các failure code ban đầu:

```text
INVALID_INPUT
PRECONDITION_FAILED
UNSUPPORTED_FRAME
LOCALIZATION_LOST
TIMEOUT
EXECUTION_ERROR
TARGET_NOT_REACHED
VERIFICATION_FAILED
BOUNDARY_VIOLATION
AGENT_JOB_FAILED
```

Mỗi failure cần:

```text
failure code
reason
evidence
timestamp
attempt_id
recovery eligibility
```

---

# 11. Repository hoàn chỉnh

```text
uav-capability-framework/
│
├── config/
│   ├── ai.yaml
│   ├── experiment.yaml
│   └── boundaries.yaml
│
├── specs/
│   └── NAV-001.yaml
│
├── src/
│   ├── core/
│   │   ├── base_capability.py
│   │   ├── result.py
│   │   ├── errors.py
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
├── ai/
│   ├── roles/
│   │   ├── implementation.md
│   │   ├── test_generation.md
│   │   └── recovery.md
│   ├── prompts/
│   └── policies/
│
├── orchestration/
│   ├── runner.py
│   ├── state_machine.py
│   ├── boundary_check.py
│   └── failure_package.py
│
├── user_interface/
│   ├── cli.py
│   ├── input_schema.py
│   └── input_builder.py
│
├── tests/
│   ├── unit/
│   └── acceptance/
│
├── scenarios/
│   └── NAV-001/
│
├── runs/
│
├── reports/
│
└── visualization/
```

---

# 12. PHASE 0 — Khóa bài toán

## Task 0.1 — Viết NAV-001

Phải định nghĩa:

```text
Input
Output
Preconditions
Failure conditions
Acceptance criteria
Evidence source
Boundary
```

## Task 0.2 — Freeze acceptance criteria

Ví dụ:

```text
position_error <= 0.5 m
elapsed <= 30 s
telemetry_valid = true
```

## Task 0.3 — Định nghĩa writable boundary

Agent được sửa:

```text
src/capabilities/navigation/
```

Agent có thể sửa có giới hạn:

```text
src/core/
```

Agent không được sửa:

```text
specs/
tests/acceptance/
src/verification/
runs/<previous attempts>/
```

## Gate Phase 0

```text
Specification complete       PASS
Input schema defined         PASS
Output schema defined        PASS
Acceptance frozen            PASS
Failure model defined        PASS
Boundary defined             PASS
```

---

# 13. PHASE 1 — Chuẩn hóa Project Skeleton

Tạo:

```text
BaseCapabilityModule
CapabilityResult
Error Model
CapabilityRegistry
UAVAdapter interface
TelemetryRecorder interface
Verifier interface
Experiment Runner
Boundary Checker
```

## Gate Phase 1

```text
Repository structure valid   PASS
Core imports work            PASS
Config loading works         PASS
Basic unit tests run         PASS
```

---

# 14. PHASE 2 — Thiết lập Local AI Environment

## Task 2.1 — Cài Ollama

Kiểm tra:

```text
Ollama service available
Model listing works
Inference works
```

Baseline:

```text
qwen2.5-coder:3b
```

## Task 2.2 — Cài và cấu hình OpenCode

Luồng:

```text
OpenCode
   ↓
Ollama endpoint
   ↓
Local LLM
```

## Task 2.3 — Test Agent trên sandbox repository

Không dùng UAV repo ngay.

Test nhỏ:

```text
Read permitted file
Create permitted file
Run harmless test
Return changed-file list
```

## Task 2.4 — Tạo Agent Role Profiles

### Implementation profile

```text
Read:
spec + allowed APIs

Write:
implementation scope

Forbidden:
spec/verifier/acceptance
```

### Test profile

```text
Read:
spec

Write:
candidate test area

Forbidden:
frozen acceptance after approval
```

### Recovery profile

```text
Read:
failure package + current implementation

Write:
implementation scope

Forbidden:
spec/verifier/acceptance/evidence
```

## Gate Phase 2

```text
Ollama ready                 PASS
Baseline model ready         PASS
OpenCode connected           PASS
Implementation role works    PASS
Test role works              PASS
Recovery role works          PASS
Agent run logging works      PASS
```

---

# 15. PHASE 3 — Simulator Baseline

Giai đoạn này chưa dùng AI để phát triển UAV capability.

Mục tiêu:

```text
Python
   ↓
PX4 SITL
   ↓
Simulated UAV
   ↓
Telemetry
```

Phải kiểm chứng:

```text
Start simulator       PASS
Vehicle detected      PASS
Read position         PASS
Read state            PASS
Telemetry stream      PASS
Stop simulator        PASS
```

Hai chế độ:

## Automated

```text
Headless / fast SITL
```

dùng cho:

```text
batch test
repeatability
agent recovery loop
```

## Visual

```text
PX4 SITL + Gazebo
```

dùng cho:

```text
3D observation
demo
replay
comparison
```

## Gate Phase 3

```text
SITL stable             PASS
Telemetry stable        PASS
Restart reproducible    PASS
Gazebo visual works     PASS
```

---

# 16. PHASE 4 — Xây UAVAdapter

Kiến trúc:

```text
GoToTargetModule
       ↓
    UAVAdapter
       ↓
      PX4
```

Interface:

```python
class UAVAdapter:
    def connect(...)
    def get_state(...)
    def get_position(...)
    def send_position_target(...)
    def abort(...)
```

Không để PX4-specific logic rải trong capability.

## Gate Phase 4

```text
Connect               PASS
Read state            PASS
Read position         PASS
Send target API       PASS
Abort API             PASS
```

---

# 17. PHASE 5 — Xây Evidence Layer

Mỗi attempt tạo:

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
            ├── agent_job.json
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

## Gate Phase 5

```text
Telemetry file generated      PASS
Timestamp valid               PASS
Position valid                PASS
Attempt metadata complete     PASS
Evidence immutable after run  PASS
```

---

# 18. PHASE 6 — Xây Independent Verifier

Verifier đọc:

```text
input.json
telemetry.csv
frozen NAV-001 spec
```

Không dùng:

```text
module_output.status
```

để quyết định PASS.

## Position check

Target:

\[
P_t=(x_t,y_t,z_t)
\]

Actual:

\[
P_f=(x_f,y_f,z_f)
\]

Error:

\[
e=\sqrt{(x_f-x_t)^2+(y_f-y_t)^2+(z_f-z_t)^2}
\]

PASS:

\[
e\leq tolerance
\]

## Timeout check

```text
elapsed <= timeout
```

## Evidence check

```text
telemetry exists
timestamp valid
localization valid
```

Output:

```json
{
  "task": "NAV-001-T01",
  "position_error_m": 0.16,
  "checks": {
    "position": "PASS",
    "timeout": "PASS",
    "telemetry": "PASS"
  },
  "overall": "PASS"
}
```

## Gate Phase 6

```text
Known PASS case detected correctly   PASS
Known FAIL case detected correctly   PASS
Module status ignored                PASS
Verifier reproducible                PASS
```

---

# 19. PHASE 7 — Implement BaseCapabilityModule

Tạo lifecycle:

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

Shared components:

```text
Input validation
Precondition handling
Timeout handling
Result model
Error model
Lifecycle state
Logging hook
```

## Gate Phase 7

```text
Lifecycle test         PASS
Invalid input test     PASS
Precondition test      PASS
Timeout test           PASS
```

---


# 19.1. PHASE 7A — Xây User Runtime Input Interface

Phase này biến runtime input thành chức năng chính thức của framework.

## Task 7A.1 — Input Schema

Tạo schema cho:

```text
target_position_m
position_tolerance_m
timeout_s
frame
task_id
scenario_id
```

Phân biệt:

```text
User-provided fields
System-observed fields
Frozen-spec fields
```

## Task 7A.2 — CLI Input

Xây:

```text
user_interface/cli.py
```

CLI nhận tham số từ người dùng và chuyển thành canonical input object.

Ví dụ:

```bash
python -m user_interface.cli \
  --target-x 10 \
  --target-y 5 \
  --target-z -3 \
  --tolerance 0.5 \
  --timeout 30
```

## Task 7A.3 — Input Validation

Validator phải từ chối:

```text
missing values
NaN / infinite values
negative tolerance
non-positive timeout
unsupported frame
target ngoài vùng test
```

## Task 7A.4 — Generate input.json

Sau validation:

```text
User Input
   ↓
Canonical Input
   ↓
input.json
```

File này trở thành artifact chính thức của experiment.

## Task 7A.5 — Connect to Experiment Runner

Luồng:

```text
CLI
 ↓
Input Builder
 ↓
Experiment Runner
 ↓
Capability
 ↓
Simulator
```

## Task 7A.6 — User Input Auditability

Mỗi run phải lưu:

```text
raw_user_input
canonical_input
validation_result
input.json
```

để có thể truy ngược người dùng đã yêu cầu gì.

## Gate Phase 7A

```text
CLI accepts valid input            PASS
Invalid input rejected             PASS
input.json generated               PASS
System state not user-spoofable    PASS
Experiment starts from CLI         PASS
Input artifacts auditable          PASS
```

---

# 20. PHASE 8 — Manual Known-Good GoToTarget Baseline

Trước khi AI tự implement, con người tạo một baseline tối thiểu.

Ví dụ:

```text
Start:
(0,0,-2)

Target:
(10,5,-3)

Tolerance:
0.5 m
```

Mục tiêu:

```text
Simulator works
Adapter works
Telemetry works
Verifier works
```

Không cần code đẹp.

Đây là control group.

## Gate Phase 8

```text
Manual candidate executes       PASS
Telemetry collected             PASS
Verifier returns expected PASS  PASS
Repeated run stable             PASS
```

---

# 21. PHASE 9 — AI Implementation Experiment

Python Orchestrator tạo Implementation Job.

Input:

```text
Frozen NAV-001
BaseCapability API
UAVAdapter API
Allowed code context
Boundary policy
```

Flow:

```text
Implementation Job
      ↓
OpenCode
      ↓
Ollama
      ↓
Local LLM
      ↓
Candidate Patch
      ↓
Changed-file manifest
      ↓
Boundary Check
```

Nếu boundary FAIL:

```text
REJECT
```

Nếu PASS:

```text
Unit Test
   ↓
SITL Experiment
   ↓
Telemetry
   ↓
Verifier
```

Kết quả:

```text
PASS → ACCEPT
FAIL → Failure Package
```

## Agent metadata

Mỗi job phải lưu:

```json
{
  "role": "implementation",
  "provider": "ollama",
  "model": "configured-model",
  "task": "NAV-001",
  "attempt": 1,
  "changed_files": [],
  "commands": [],
  "summary": ""
}
```

## Gate Phase 9

```text
Agent generated candidate       PASS
Boundary check executed         PASS
Candidate testable              PASS
Agent metadata complete         PASS
```

---

# 22. PHASE 10 — Recovery Experiment

Đây là experiment chính của PoC.

Cố tình tạo hoặc sử dụng một implementation lỗi.

Ví dụ bug:

```text
GoalChecker chỉ tính XY
```

thay vì:

```text
XYZ
```

Scenario:

```text
Target:
(10,5,-5)

Actual:
(10,5,-3)
```

Module lỗi:

```text
XY error = 0
SUCCESS
```

Verifier:

```text
3D error = 2 m
FAIL
```

Attempt 1:

```text
Provider       PASS
Boundary       PASS
Execution      PASS
Verification   FAIL
```

Orchestrator tạo:

```text
failure.json
```

và `Failure Package`.

Recovery Job:

```text
Failure Package
      ↓
OpenCode
      ↓
Local LLM
      ↓
Recovery Patch
      ↓
Boundary Check
      ↓
Rerun
```

Attempt 2:

```text
Provider       PASS
Boundary       PASS
Execution      PASS
Verification   PASS
```

Cuối cùng:

```text
Candidate Accepted
```

## Gate Phase 10

```text
Verifier detects wrong implementation   PASS
Failure package generated               PASS
Recovery job triggered                  PASS
Forbidden files unchanged               PASS
Patch rerun automatically               PASS
Final verified candidate accepted       PASS
```

---

# 23. PHASE 11 — Test Suite

Bộ test ban đầu:

| ID | Scenario | Expected |
|---|---|---|
| T01 | Target thông thường | PASS |
| T02 | Thay đổi X/Y/Z | PASS |
| T03 | Target rất gần | PASS |
| T04 | Input thiếu thành phần | REJECT |
| T05 | Frame không hỗ trợ | REJECT |
| T06 | Localization invalid | PRECONDITION_FAILED |
| T07 | Timeout | FAIL |
| T08 | Module báo success sai | VERIFIER FAIL |
| T09 | Agent sửa spec | BOUNDARY FAIL |
| T10 | Agent sửa verifier | BOUNDARY FAIL |
| T11 | User tự nhập target hợp lệ | PASS |
| T12 | User nhập tolerance âm | INVALID_INPUT |
| T13 | User nhập frame không hỗ trợ | INVALID_INPUT |
| T14 | User cố nhập giả `localization_valid=true` | REJECT / IGNORE USER-SPOOFED SYSTEM STATE |

Mục tiêu:

> Kiểm tra cả hành vi đúng lẫn hành vi từ chối đúng.

---

# 24. PHASE 12 — Visualization V1

Sau mỗi run sinh:

```text
trajectory.png
position_error.png
verification_summary.png
report.md
```

## Trajectory

Show:

```text
START ●─────────────→ X TARGET
          actual
         trajectory
```

## Error over time

```text
12m │●
    │ \
 8m │  \
    │   \
 4m │    \
    │      \
0.5m│---------- threshold
    │         \
 0m │          ●
    └────────────────→ time
```

Visualization đọc cùng telemetry với verifier.

---

# 25. PHASE 13 — Live Dashboard

Chỉ làm sau khi offline pipeline ổn định.

Dashboard cần show:

```text
INPUT
Target
Tolerance
Timeout

LIVE STATE
Current position
Velocity
Distance to target
Elapsed time

VERIFICATION
Position check
Timeout check
Telemetry check

RESULT
PASS / FAIL
```

Dashboard chỉ hiển thị.

Không có quyền sửa verdict.

---

# 26. PHASE 14 — Replay và Attempt Comparison

Cần show rõ sự khác nhau trước/sau recovery.

```text
ATTEMPT 1                 ATTEMPT 2

Error: 0.83 m             Error: 0.18 m
FAIL                      PASS
```

Kèm:

```text
Failure reason
Recovery hypothesis
Files changed
Spec changed: NO
Verifier changed: NO
Acceptance changed: NO
```

Đây là bằng chứng trực quan quan trọng nhất khi demo AI Recovery.

---

# 27. PHASE 15 — Experiment Matrix

Không tuyên bố module đáng tin chỉ vì một lần PASS.

Ví dụ:

```text
T01 × 10
T02 × 10
T03 × 10
...
```

Metrics:

```text
Total runs
Passed
Failed
Success rate
Mean position error
Max position error
Mean duration
First-attempt pass rate
Recovery success rate
Mean recovery attempts
Boundary violation count
Invalid patch count
```

---

# 28. Benchmark Local Model

Sau khi framework ổn định, có thể thay model.

```text
OpenCode
   │
   ├── Model A
   ├── Model B
   └── Model C
```

Dùng cùng:

```text
NAV-001
Test Suite
Simulator
Verifier
Boundary Policy
```

So sánh:

```text
First-attempt acceptance rate
Recovery success rate
Average attempts
Boundary violations
Invalid patches
Agent execution time
Final verified correctness
```

Như vậy mới đánh giá được model nào phù hợp.

---

# 29. Quản lý tài nguyên Local AI

Local LLM và Gazebo/PX4 có thể cạnh tranh tài nguyên.

Khuyến nghị:

```text
Agent coding step
    ↓
Agent job kết thúc
    ↓
Run simulation
```

Không cần chạy inference nặng liên tục trong flight experiment.

Log:

```text
model
agent duration
CPU memory
GPU memory nếu đo được
simulation duration
experiment result
```

Nếu thiếu tài nguyên:

```text
1. Giảm context.
2. Giảm file đưa vào Agent.
3. Chia task nhỏ hơn.
4. Dùng model nhỏ hơn.
5. Tối ưu simulation mode.
```

Không được:

```text
Nới acceptance criteria
```

để bù cho AI yếu.

---

# 30. Fallback Strategy nếu Local Model chưa đủ mạnh

Thứ tự:

```text
1. Giữ nguyên spec/verifier.
2. Cải thiện prompt.
3. Cải thiện context selection.
4. Chia task Agent nhỏ hơn.
5. Tăng chất lượng failure package.
6. Thử local model khác.
7. Benchmark cloud model riêng nếu dự án cho phép.
```

Điểm quan trọng:

> Không thay đổi tiêu chuẩn kỹ thuật chỉ vì model AI chưa đủ tốt.

---

# 31. Artifact bắt buộc mỗi Experiment

```text
raw_user_input.json
input_validation.json
input.json
agent_job.json
changed_files.json
boundary_result.json
unit_test_result.json
telemetry.csv
events.jsonl
module_output.json
verification.json
trajectory.png
position_error.png
report.md
```

Recovery attempt thêm:

```text
failure.json
recovery_job.json
recovery_patch_summary.json
```

---

# 32. Report cuối

Report cần chia rõ:

## REQUESTED

```text
Target
Tolerance
Timeout
Frame
```

## OBSERVED

```text
Final position
Elapsed time
Telemetry state
```

## VERIFIED

```text
Position error
Threshold
Timeout check
Evidence check
Overall PASS/FAIL
```

## AI ACTIVITY

```text
Model
Role
Attempt
Files changed
Boundary result
Recovery count
```

---

# 33. Màn hình demo mong muốn

```text
================================================
           UAV CAPABILITY TEST
================================================

Module:
NAV-001 GoToTarget

AGENT
-----------------------------------------------
Runner            OpenCode
Provider          Ollama
Model             configured local model
Role              Implementation / Recovery
Attempt           2

INPUT
-----------------------------------------------
Input Source      USER
Start             (0.00, 0.00, -2.00)
Target            (10.00, 5.00, -3.00)
Tolerance         0.50 m
Timeout           30 s

EXECUTION
-----------------------------------------------
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
Boundary                 PASS

FINAL RESULT
-----------------------------------------------

                 PASS

================================================
```

Bên cạnh cần show:

```text
3D simulator
Trajectory graph
Error graph
Attempt history
Recovery diff
```

---

# 34. Definition of Done — GoToTargetModule v1.0

Chỉ hoàn thành khi toàn bộ điều kiện sau PASS:

| Nhóm | Điều kiện |
|---|---|
| Specification | PASS |
| Input schema | PASS |
| User CLI input | PASS |
| User input validation | PASS |
| input.json generation | PASS |
| System-state anti-spoofing | PASS |
| Preconditions | PASS |
| Failure model | PASS |
| BaseCapabilityModule | PASS |
| UAVAdapter | PASS |
| PX4 SITL | PASS |
| Gazebo visual | PASS |
| Telemetry recording | PASS |
| Evidence immutability | PASS |
| Independent verifier | PASS |
| Acceptance test suite | PASS |
| OpenCode local connection | PASS |
| Ollama model invocation | PASS |
| Implementation Job | PASS |
| Agent metadata logging | PASS |
| Boundary enforcement | PASS |
| Forbidden edit rejection | PASS |
| Failure detection | PASS |
| Recovery Job | PASS |
| Rerun automation | PASS |
| Final verified candidate | PASS |
| Visualization | PASS |
| Report generation | PASS |
| Repeatability | PASS |

---

# 35. Capability Template sau PoC

Sau khi `GoToTargetModule` hoàn thiện, rút ra:

```text
CapabilityTemplate
```

Template gồm:

```text
Specification template
Input schema template
Failure model template
Base class
Adapter interface
Telemetry contract
Verifier interface
Agent role contract
Boundary policy
Experiment runner
Artifact structure
Visualization
Report
```

Mỗi module mới không cần xây lại framework.

---

# 36. Thứ tự các module tiếp theo

Khuyến nghị:

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

ta có thể tạo mission:

```text
Takeoff
   ↓
GoToTarget
   ↓
Land
```

Sau đó xây:

```text
MissionExecutionModule
```

---

# 37. Hướng mở rộng theo Level UAV

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

Level 4 thêm:

```text
EventDetector
ReplanningModule
RecoveryModule
```

Level 5 thêm:

```text
GoalInterpreter
RequirementModule
TaskDecomposer
CapabilityRouter
High-Level Orchestrator
```

---

# 38. Khi nào mới chuyển sang Multi-Agent thật?

Chưa cần ở PoC đầu.

PoC đầu:

```text
One Local Model
+
Multiple Independent Roles
```

Chỉ khi framework ổn định mới xem xét:

```text
Planner Model
Implementation Model
Test Model
Recovery Model
```

hoặc các agent chạy đồng thời.

Điều kiện để nâng lên Multi-Agent thật:

```text
Single-module workflow stable
Boundary stable
Verifier stable
Recovery stable
Capability Registry stable
Several modules validated
Mission composition needed
```

---

# 39. Workflow triển khai thực tế

```text
STEP 01  Freeze NAV-001
STEP 02  Create repository skeleton
STEP 03  Define boundary policy
STEP 04  Install/configure Ollama
STEP 05  Load baseline model
STEP 06  Connect OpenCode → Ollama
STEP 07  Configure Implementation/Test/Recovery roles
STEP 08  Validate Agent on sandbox repo
STEP 09  Build PX4 SITL baseline
STEP 10  Build Gazebo visual environment
STEP 11  Build UAVAdapter
STEP 12  Build TelemetryRecorder
STEP 13  Build Independent Verifier
STEP 14  Build BaseCapabilityModule
STEP 15  Define User Runtime Input Schema
STEP 16  Build CLI User Input Interface
STEP 17  Build Input Validation + input.json generation
STEP 18  Connect User Input → Experiment Runner
STEP 19  Build manual known-good GoToTarget baseline
STEP 20  Run Implementation Agent Job
STEP 21  Boundary check candidate
STEP 22  Run unit tests
STEP 23  Run user-defined SITL experiment
STEP 24  Collect evidence
STEP 25  Run deterministic verifier
STEP 26  If FAIL → build Failure Package
STEP 27  Run Recovery Agent Job
STEP 28  Boundary check recovery patch
STEP 29  Rerun same frozen user testcase
STEP 30  Accept only after verifier PASS
STEP 31  Generate offline visualization
STEP 32  Generate final report
STEP 33  Run full test suite
STEP 34  Run experiment matrix
STEP 35  Add optional UI/dashboard input form
STEP 36  Benchmark alternative local models
STEP 37  Package GoToTarget as CapabilityTemplate
STEP 38  Start next capability
```

---

# 40. Kiến trúc mục tiêu của PoC

```text
                   NAV-001
                      │
                      ▼
             Python Orchestrator
                      │
                      ▼
             Implementation Job
                      │
                   OpenCode
                      │
                    Ollama
                      │
                  Local LLM
                      │
                      ▼
              Candidate Patch
                      │
                      ▼
              Boundary Checker
                /          \
             FAIL          PASS
              │             │
            Reject       Unit Tests
                            │
                            ▼
                       PX4 SITL
                            │
                         Gazebo
                            │
                            ▼
                       Telemetry
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
             Visualizer          Python Verifier
                                       │
                                  PASS / FAIL
                                  /         \
                               PASS         FAIL
                                │             │
                             ACCEPT      Failure Package
                                              │
                                              ▼
                                        Recovery Job
                                              │
                                           OpenCode
                                              │
                                           Ollama
                                              │
                                          Local LLM
                                              │
                                              ▼
                                             Patch
                                              │
                                              └──→ Boundary → Rerun
```

---

# 41. Kết luận triển khai

Mốc thành công của phase đầu **không phải chỉ là UAV bay được trong simulator**.

Mốc cần chứng minh là:

> Một Local LLM chạy thông qua OpenCode có thể thực hiện các Agent Job độc lập để triển khai và sửa một UAV capability dựa trên specification cố định; Python Orchestrator kiểm soát workflow và boundary; PX4 SITL/Gazebo tạo môi trường thực nghiệm; Telemetry Recorder thu evidence độc lập; Python Verifier quyết định PASS/FAIL bằng tiêu chí kỹ thuật cố định; khi implementation sai, Recovery Job tạo patch mới nhưng không được sửa specification, verifier hoặc acceptance criteria; candidate chỉ được chấp nhận khi verifier PASS.

Nếu `GoToTargetModule` đạt được chu trình trên, đồng thời cho phép người dùng tự nhập runtime testcase qua interface chuẩn mà không sửa code, nó sẽ trở thành **reference implementation** cho UAV Capability Framework.

Từ đó, các capability mới chỉ cần kế thừa chu trình:

```text
SPEC
 ↓
AI IMPLEMENT
 ↓
BOUNDARY
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

Đây là nền tảng để mở rộng từ capability nhỏ → mission execution → adaptive mission → goal-driven UAV autonomy.
