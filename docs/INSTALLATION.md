# Cài đặt chi tiết

## 1. Môi trường hỗ trợ

Baseline hiện tại được thiết kế cho Linux. Gazebo GUI phù hợp nhất với Ubuntu desktop/X11 hoặc XWayland. Headless server không cần desktop display.

Yêu cầu tối thiểu:

- Python 3.10+
- `python3-venv`
- Docker Engine/Desktop chạy được bởi user hiện tại
- Git/Unzip nếu clone hoặc dùng release ZIP
- `xauth` nếu mở Gazebo GUI
- dung lượng trống đủ cho PX4/Gazebo Docker image và evidence

Kiểm tra nhanh:

```bash
python3 --version
docker --version
docker ps
git --version
```

Gazebo GUI:

```bash
echo "$DISPLAY"
command -v xauth
```

## 2. Cài từ GitHub clone

```bash
git clone https://github.com/OWNER/REPOSITORY.git
cd REPOSITORY
./uavctl install --pull-sim
```

`--pull-sim` tải trước image PX4/Gazebo đã pin. Có thể bỏ flag này và để image được tải khi chạy lần đầu.

## 3. Cài từ GitHub Release ZIP

```bash
unzip remote_UAV_v1_0_0_deploy.zip
cd remote_UAV_v1_0_0_deploy
./uavctl install --pull-sim
```

## 4. Health check

Không yêu cầu GUI:

```bash
./uavctl doctor
```

Bắt buộc kiểm tra desktop Gazebo:

```bash
./uavctl doctor --gui
```

Nếu có lỗi, chạy các lệnh trong `docs/TROUBLESHOOTING.md` trước khi mở web.

## 5. Khởi động

Desktop Gazebo:

```bash
./uavctl start --gui
```

Headless Gazebo physics:

```bash
./uavctl start --headless
```

Backend SIH tương thích nhẹ hơn:

```bash
./uavctl start --sih
```

Đổi cổng:

```bash
./uavctl start --gui --port 8371
```

## 6. URL

Mặc định:

```text
Single Flight  http://127.0.0.1:8370
Mission        http://127.0.0.1:8370/mission
```

Server chủ ý chỉ phục vụ localhost trong baseline hiện tại.

## 7. Developer installation

```bash
./uavctl install --dev
./uavctl test
```

`test-uav` tự vô hiệu hóa pytest plugin autoload từ ROS/PYTHONPATH để test của project tái lập được.

## 8. Runtime data

Cài mới tạo `uav.env` và mặc định dùng:

```text
~/.local/share/remote-uav/runs/
```

Không xóa thư mục này khi chỉ muốn nâng cấp source.

## 9. Nâng cấp

Khuyến nghị:

1. Dừng server.
2. Backup `uav.env` nếu đã tùy chỉnh.
3. Clone/extract source version mới vào thư mục mới.
4. Chạy `./uavctl install`.
5. Giữ `UAV_DATA_DIR` trỏ về data cũ.
6. Chạy `./uavctl test` và `./uavctl doctor --gui`.
7. Test 3 leg Single Flight + 1 Mission reference.

Nếu dữ liệu cũ còn nằm trong `<old-project>/runs`:

```bash
./uavctl migrate-data /path/to/old-project
./uavctl migrate-data /path/to/old-project --apply
```

Lệnh đầu là dry-run.
