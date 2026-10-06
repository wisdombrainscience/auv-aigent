# Quick Start — 10 phút

## 1. Cài và kiểm tra

```bash
./uavctl install --pull-sim
./uavctl doctor --gui
```

## 2. Mở hệ thống

```bash
./uavctl start --gui
```

Mở `http://127.0.0.1:8370`.

## 3. Single Flight đầu tiên

Trong **Initial UAV position**:

```text
North 0
East  0
Altitude 0
```

Bấm **Start / Restart Simulation**. Chờ:

```text
Gazebo RUNNING
Simulation RUNNING
Session READY
```

Chọn target:

```text
North 5
East  3
Altitude 2
```

Bấm **Bay tới điểm đích**.

Web sẽ đi qua:

```text
AUTO TAKEOFF → STABILIZING → NAV RUNNING → VERIFYING → PASS/FAIL
```

Sau PASS, nhập target thứ hai và bấm bay tiếp. Simulator/Gazebo không cần restart.

## 4. Land

Bấm **Land**. Sau khi landed, có thể chọn target mới; Single Flight sẽ tự bootstrap takeoff lại trước NAV.

## 5. Mission đầu tiên

Mở `http://127.0.0.1:8370/mission`.

Ví dụ:

```text
Home: N0 E0
Takeoff: Alt3
WP1: N5 E0 Alt3
WP2: N5 E5 Alt3
WP3: N0 E5 Alt3
Return Home: Alt3
```

Bấm **Run Mission**. Kỳ vọng từng step PASS và mission cuối PASS.

## 6. Sau khi chạy

```bash
./uavctl storage status
```

Evidence/history nằm trong `UAV_DATA_DIR/runs` (mặc định `~/.local/share/remote-uav/runs`).
