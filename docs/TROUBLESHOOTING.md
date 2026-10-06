# Troubleshooting

## Chẩn đoán nhanh

```bash
./uavctl doctor --gui
./uavctl version
docker ps -a --filter name=nav001-step37a-sim
```

Simulator log:

```bash
DATA_DIR="${UAV_DATA_DIR:-$HOME/.local/share/remote-uav}"
tail -100 "$DATA_DIR/runs/.runtime/simulator.log"
```

Nếu installation cũ chưa externalize data, log có thể nằm ở `<project>/runs/.runtime/simulator.log`.

## Docker daemon/permission

Triệu chứng:

```text
permission denied while trying to connect to Docker daemon
```

Kiểm tra:

```bash
docker ps
```

Project cần Docker hoạt động với user hiện tại, không dựa vào sudo tương tác bên trong web runtime.

## Gazebo UNAVAILABLE / không mở cửa sổ

```bash
echo "$DISPLAY"
command -v xauth
xauth nlist "$DISPLAY" | head
./uavctl doctor --gui
```

Chạy `./uavctl start --gui` từ terminal của graphical desktop session. Với SSH/headless, dùng `--headless`.

## PX4 SITL container exited during startup

```bash
tail -100 "${UAV_DATA_DIR:-$HOME/.local/share/remote-uav}/runs/.runtime/simulator.log"
docker image inspect px4io/px4-sitl-gazebo:v1.18.0-beta1-660-g61907962c9
```

Nếu image thiếu:

```bash
./uavctl pull-sim
```

## Port 8370 đã được dùng

```bash
ss -ltnp | grep ':8370'
```

Chạy cổng khác:

```bash
./uavctl start --gui --port 8371
```

## Session RESET_REQUIRED

Thường xảy ra sau FAIL hoặc sau khi Mission đã reset/own simulator. Bấm **Start / Restart Simulation** để tạo session authoritative mới.

## Single Flight không bay target tiếp theo

Baseline v1.0.0 hỗ trợ continuous targets. Sau PASS, chọn target mới và bấm Bay tiếp. Nếu build cũ không tự CONTINUE, kiểm tra:

```bash
./uavctl version
```

Dùng release hiện tại thay vì chồng patch cũ.

## UAV đến target nhưng verifier FAIL sát tolerance

Current runtime có settling/hysteresis để giảm overshoot, nhưng verifier vẫn dùng evidence thực tế. Kiểm tra `verification.json` và terminal trajectory. Không nên đơn giản tăng tolerance để che dynamics nếu chưa đánh giá acceptance requirement.

## Mission Return Home PASS nhưng Land FAIL do drift

Current LAND handoff thực hiện Hold/velocity settling trước Land. Nếu còn xảy ra, lưu attempt evidence và simulator log để phân tích dynamics; không sửa verifier trước khi biết nguyên nhân.

## `User callback queue slow`

Baseline hiện dùng persistent telemetry subscriptions/cache. Nếu warning xuất hiện dày:

1. xác nhận đang chạy version hiện tại;
2. dừng server sạch;
3. kiểm tra MAVSDK process còn tồn dư;
4. chạy regression/soak trước khi tiếp tục.

```bash
pgrep -af mavsdk_server || echo "NO MAVSDK SERVER LEFT"
```

## Pytest bị ROS plugin làm lỗi

Dùng:

```bash
./uavctl test
```

Không chạy pytest trực tiếp trong ROS shell nếu không cần. Wrapper đã disable third-party plugin autoload.

## Storage full / new run bị block

```bash
./uavctl storage status
./uavctl storage cleanup
```

Review dry-run rồi mới:

```bash
./uavctl storage cleanup --apply
```

Pinned evidence không bị cleanup.

## Build/source bị trộn nhiều patch

Dấu hiệu: version không khớp, dataclass/API mismatch, test fail hàng loạt sau patch. Giải pháp khuyến nghị: clone/extract release sạch và giữ data ở `UAV_DATA_DIR` thay vì sửa tiếp folder cũ.

## pip reports ROS dependency conflicts during setup

If a ROS 2 shell exports `PYTHONPATH`, older releases could make pip see ROS
packages such as `launch-ros` or `generate-parameter-library-py` while installing
the project's private virtual environment. v1.0.1 isolates venv/pip commands
from host `PYTHONPATH` and user-site packages, then runs `pip check`.

These warnings were not requirements of Remote UAV itself. Do not install the
entire ROS Python dependency chain into `.venv` just to silence them. Upgrade to
v1.0.1 and re-run `./setup-uav` (or `./uavctl install --dev`).
