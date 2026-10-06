# Contributing

Cảm ơn bạn muốn đóng góp.

## Trước khi mở PR

```bash
./uavctl install --dev
./uavctl test
./uavctl github-check
```

## Quy tắc

- Không thay đổi verifier/frozen acceptance chỉ để làm implementation PASS.
- Không commit runtime evidence, local environment, secrets hoặc `.venv`.
- Giữ PX4-specific logic sau adapter boundary.
- Mỗi behavior change cần regression test tương ứng.
- Mô tả rõ commands đã chạy và kết quả validation.

## Branch/PR

Gợi ý branch:

```text
feature/...
fix/...
docs/...
```

PR nên ghi:

- mục tiêu;
- file chính thay đổi;
- ảnh hưởng đến capability/mission/evidence;
- test đã chạy;
- SITL/Gazebo acceptance nếu có runtime change.
