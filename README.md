# Remote UAV Mission Console

Nền tảng UAV **simulation-only** chạy PX4 SITL + Gazebo, hỗ trợ Single Flight, Mission Builder nhiều waypoint, capability runtime tổng quát, deterministic verifier, immutable evidence và quản lý tài nguyên cục bộ.

> **Phạm vi an toàn:** dự án hiện chỉ hỗ trợ mô phỏng PX4 SITL/Gazebo. Không dùng trực tiếp để điều khiển UAV thật.

## Tính năng chính

- **Single Flight**: chọn target bằng North / East / Altitude hoặc click bản đồ, tự takeoff từ mặt đất, bay nhiều target liên tiếp trong cùng session, Hold và Land.
- **Mission Builder**: Takeoff → nhiều waypoint → Return Home → Land, hiển thị tiến trình và trajectory live.
- **Gazebo Desktop**: xem UAV bay đồng thời trên web và trong Gazebo.
- **Capability framework**: `TAKEOFF-001`, `NAV-001`, `RETURN-HOME-001`, `LAND-001` chạy qua registry/executor chung.
- **Deterministic verification**: PASS/FAIL do Python verifier độc lập quyết định, không do AI/UI/module tự đánh giá.
- **Mission verification**: kiểm tra child verdict, evidence integrity, continuity, terminal home và landed state.
- **Evidence**: telemetry được freeze trước verification; artifact được hash/finalize và lưu lịch sử.
- **Tối ưu tài nguyên**: bounded RAM buffers, SQLite metadata, telemetry delta + gzip, disk quota/retention và dữ liệu runtime tách khỏi source.

## Cài nhanh

### Yêu cầu

- Linux (khuyến nghị Ubuntu desktop cho Gazebo GUI)
- Python 3.10+
- Docker chạy được bằng user hiện tại (`docker ps` không cần sudo tương tác)
- `xauth` nếu muốn mở cửa sổ Gazebo desktop

### Cài production

```bash
./uavctl install --pull-sim
./uavctl doctor --gui
./uavctl start --gui
```

Mở:

- Single Flight: `http://127.0.0.1:8370`
- Mission Builder: `http://127.0.0.1:8370/mission`

### Chạy headless

```bash
./uavctl install
./uavctl doctor
./uavctl start --headless
```

### Developer setup

```bash
./uavctl install --dev
./uavctl test
```

Baseline release hiện tại: **v1.0.1**. Regression suite: **194 tests PASS** trong build deployment baseline.

## Workflow sử dụng

### Single Flight

1. `Start / Restart Simulation` với Initial Altitude = `0`.
2. Chọn target, ví dụ `N 5 / E 3 / Alt 2`.
3. Bấm **Bay tới điểm đích**.
4. Hệ thống thực hiện: `AUTO TAKEOFF → STABILIZING → NAV RUNNING → VERIFYING → PASS/FAIL`.
5. Sau PASS, nhập target mới và bay tiếp mà không restart Gazebo.
6. Bấm **Land** khi muốn kết thúc.

### Mission Builder

1. Mở `/mission`.
2. Chọn Home, Takeoff Altitude, Return Altitude.
3. Thêm waypoint bằng số hoặc click bản đồ.
4. Bấm **Run Mission**.
5. Theo dõi từng step: Takeoff → WP1..N → Return Home → Land.
6. Kết quả cuối do MissionVerifier quyết định.

## Các lệnh quan trọng

```bash
./uavctl install [--dev] [--pull-sim]
./uavctl start [--gui|--headless] [--gazebo|--sih] [--port N]
./uavctl doctor [--gui]
./uavctl test
./uavctl github-check
./uavctl storage status
./uavctl storage cleanup          # dry-run
./uavctl storage cleanup --apply
./uavctl package
./uavctl migrate-data <old-project> [--apply]
./uavctl version
```

Các entrypoint cũ (`start-uav`, `test-uav`, `mission-uav`, `capability-uav`, `soak-uav`, ...) vẫn được giữ để tương thích.

## Dữ liệu và evidence

Cài đặt mới mặc định lưu runtime data tại:

```text
~/.local/share/remote-uav/runs/
```

Source code có thể nâng cấp/thay thế mà không xóa history. File `uav.env` là cấu hình local và được `.gitignore` bảo vệ.

## Tài liệu

- [Cài đặt chi tiết](docs/INSTALLATION.md)
- [Quick Start 10 phút](docs/QUICKSTART.md)
- [Hướng dẫn sử dụng đầy đủ](docs/USER_MANUAL_VI.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Kiến trúc hệ thống](docs/ARCHITECTURE.md)
- [Developer Guide](docs/DEVELOPER_GUIDE.md)
- [Đưa dự án lên GitHub](docs/GITHUB_PUBLISHING.md)
- [Deployment & tối ưu tài nguyên](docs/DEPLOYMENT.md)
- [Gazebo GUI](docs/GAZEBO_GUI.md)
- [Danh mục tài liệu](docs/README.md)

English overview: [README.en.md](README.en.md)

## Kiểm tra trước khi public GitHub

```bash
./uavctl github-check
./uavctl test
```

Không commit `.venv/`, `runs/`, `uav.env`, file secret/key hay release ZIP vào source repository.

## Phát hành release package

```bash
./uavctl package
```

Artifact sạch được tạo trong `dist/`, không chứa `.venv`, runtime evidence, cache hay local config.

## Trạng thái và giới hạn

- Web console chỉ bind localhost theo mặc định.
- Runtime hiện là simulation-only.
- AI engineering plane không có quyền quyết định PASS/FAIL và không nằm trong realtime flight-control loop.
- Real UAV integration cần một safety/verification phase riêng trước khi được xem là supported.

## License

Repository chưa tự động áp một open-source license thay cho chủ sở hữu. Trước khi public rộng rãi, maintainer nên chọn license phù hợp (ví dụ MIT hoặc Apache-2.0) và thêm file `LICENSE`. Xem [docs/LICENSING.md](docs/LICENSING.md).
