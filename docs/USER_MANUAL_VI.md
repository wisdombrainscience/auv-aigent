# Hướng dẫn sử dụng đầy đủ — Remote UAV Mission Console

## 1. Mục tiêu của hệ thống

Remote UAV Mission Console là môi trường mô phỏng để phát triển, chạy và kiểm chứng capability UAV. PX4 SITL/Gazebo chịu trách nhiệm dynamics/flight stack; MAVSDK là adapter điều khiển/telemetry; deterministic verifier là authority cho PASS/FAIL.

AI, UI và capability module **không có quyền tự tuyên bố PASS**.

## 2. Hệ tọa độ

UI sử dụng:

- **North (m)**: hướng Bắc cục bộ.
- **East (m)**: hướng Đông cục bộ.
- **Altitude (m)**: số dương hướng lên, thân thiện với người vận hành.

Backend PX4 dùng LOCAL_NED:

```text
x = North
y = East
z = Down
```

Do đó UI `Altitude = 3 m` được chuyển nội bộ thành `z = -3 m`.

## 3. Khởi động

```bash
./uavctl start --gui
```

Chỉ mở web sau khi terminal báo server sẵn sàng. Nếu dùng GUI, health chip phải thể hiện Gazebo khả dụng/running sau khi simulator khởi động.

## 4. Single Flight

### 4.1 Start / Restart Simulation

Initial UAV position là vị trí spawn simulator, không phải điểm đầu bắt buộc nhập cho từng leg.

Khuyến nghị:

```text
North 0
East  0
Altitude 0
```

Bấm **Start / Restart Simulation** để tạo session mới.

### 4.2 Chọn target

Có thể nhập số hoặc click bản đồ 2D. Altitude luôn nhập dương trên UI.

### 4.3 Bay từ mặt đất

Nếu UAV đang landed, Single Flight thực hiện setup takeoff trước khi mở evidence window NAV:

```text
Ground
→ AUTO TAKEOFF
→ STABILIZING / HOLD
→ NAV RUNNING
→ VERIFYING
→ PASS / FAIL
```

Setup takeoff của Single Flight phục vụ vận hành tương tác; nó không thay thế verdict độc lập của `TAKEOFF-001`. Nếu cần chứng minh Takeoff capability, dùng Mission Builder hoặc `capability-uav`.

### 4.4 Bay nhiều target liên tục

Sau một NAV PASS:

1. UAV chuyển Hold và settle.
2. Hệ thống lưu verified final position và fresh current vehicle position riêng biệt.
3. Chọn target mới.
4. Backend tự chuyển session sang CONTINUE.
5. Start của leg mới lấy từ telemetry thực tế, không lấy từ tọa độ stale của browser.

Không cần restart Gazebo giữa các target.

### 4.5 PASS/FAIL

NAV-001 không chỉ chạm tolerance một lần; runtime yêu cầu settling trước khi kết thúc để giảm lỗi do quán tính. Verifier vẫn kiểm tra evidence cuối độc lập.

Nếu FAIL, không nên tiếp tục chain tự động. Restart Simulation để đưa vehicle state về trạng thái rõ ràng.

### 4.6 Land

Nút **Land** kết thúc flight session ở mặt đất. Sau Land, nếu chọn target mới, Single Flight có thể bootstrap takeoff lại.

## 5. Mission Builder

### 5.1 Mission setup

- Home North/East
- Takeoff Alt
- Return Alt
- Waypoint tolerance
- Timeout mỗi step
- Advanced tolerances khi cần

### 5.2 Waypoints

Thêm waypoint bằng nút hoặc click map. Mỗi waypoint có North/East/Altitude riêng.

### 5.3 Mission plan

Mission mặc định:

```text
TAKEOFF-001
→ NAV-001 waypoint 1
→ NAV-001 waypoint 2
→ ...
→ RETURN-HOME-001
→ LAND-001
→ MissionVerifier
```

### 5.4 Live status

Trong khi chạy, UI hiển thị:

- current capability/step;
- North/East/Altitude live;
- vehicle state;
- progress;
- planned path;
- measured trajectory.

### 5.5 Kết quả mission

Mission PASS cần các điều kiện như:

- sequence hợp lệ;
- tất cả child verdict PASS;
- child evidence integrity PASS;
- continuity giữa các step PASS;
- LAND là terminal step;
- landed state PASS;
- terminal home distance PASS.

## 6. Gazebo

Gazebo là simulator/viewer vật lý, không phải authority của verdict. Có thể chạy:

```bash
./uavctl start --gui
```

hoặc headless:

```bash
./uavctl start --headless
```

Nếu web chạy nhưng Gazebo không mở, xem `docs/TROUBLESHOOTING.md`.

## 7. Capability CLI

Ví dụ Takeoff:

```bash
./capability-uav TAKEOFF-001 \
  --input-file config/examples/takeoff_3m.json \
  --scenario TAKEOFF-DEMO \
  --reset-simulation \
  --spawn-altitude 0 \
  --post-action land \
  --stop-simulation-after
```

Mission reference:

```bash
./mission-uav \
  --mission-file config/examples/mission_reference.json \
  --scenario MISSION-DEMO \
  --reset-simulation \
  --spawn-altitude 0 \
  --stop-simulation-after
```

## 8. Evidence và history

Mỗi attempt có input, telemetry, module output, verification, hashes/finalization tùy capability. Evidence đã finalized không nên sửa bằng tay.

Metadata lịch sử được index trong SQLite để UI không phải load toàn bộ raw evidence vào RAM.

## 9. Storage

```bash
./uavctl storage status
./uavctl storage cleanup
./uavctl storage cleanup --apply
```

`cleanup` không có `--apply` chỉ preview.

Pin attempt quan trọng:

```bash
./uavctl storage pin runs/NAV-001/SCENARIO/attempt_001
```

## 10. Resource policy

File: `config/resource_policy.yaml`.

Baseline giữ raw verifier telemetry ở 10 Hz, trong khi UI dùng bounded buffer và incremental transport. Không tăng quota/timeout quá lớn nếu chưa đánh giá disk/RAM.

## 11. Dừng hệ thống

Nhấn `Ctrl+C` trong terminal chạy server. Nếu simulator container còn tồn tại bất thường:

```bash
docker ps -a --filter name=nav001-step37a-sim
```

Chỉ xóa container khi server đã dừng và bạn hiểu trạng thái session.

## 12. Nguyên tắc vận hành

- Không sửa evidence đã finalized.
- Không dùng FAIL run làm start chain mới nếu state không chắc chắn.
- Không expose localhost web server ra Internet mà chưa có authentication/TLS/security review.
- Không dùng baseline này cho UAV thật.
