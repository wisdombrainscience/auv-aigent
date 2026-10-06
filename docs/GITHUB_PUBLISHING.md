# Đưa dự án lên GitHub

Tài liệu này giả định source đã chạy `./uavctl test` thành công.

## 1. Kiểm tra trước khi commit

```bash
./uavctl github-check
./uavctl test
git status --short 2>/dev/null || true
```

Không publish:

- `.venv/`
- `runs/` / evidence cá nhân
- `uav.env`
- password/token/private key
- local ZIP/patch/debug logs

`.gitignore` đã cấu hình các nhóm này.

## 2. Chọn tên repository

Gợi ý:

```text
remote-uav-mission-console
remote-uav-capability-platform
px4-remote-uav-sim
```

Description gợi ý:

```text
PX4 SITL/Gazebo UAV capability & mission platform with deterministic verification, live web UI, immutable evidence and bounded runtime resources.
```

Topics gợi ý:

```text
uav px4 gazebo mavsdk robotics simulation python autonomy testing verification
```

## 3. Cách A — GitHub CLI

Cần cài `gh` và đăng nhập trước:

```bash
gh auth login
```

Tại project root:

```bash
git init -b main
git add .
git commit -m "Release v1.0.1"

gh repo create remote-uav-mission-console \
  --public \
  --source=. \
  --remote=origin \
  --push \
  --description "PX4 SITL/Gazebo UAV capability and mission platform with deterministic verification"
```

Nếu muốn private trước:

```bash
gh repo create remote-uav-mission-console --private --source=. --remote=origin --push
```

## 4. Cách B — Git + GitHub web

Trên GitHub tạo một repository **trống**. Nếu đang import source local, không cần tạo sẵn README/.gitignore/license trên remote.

Sau đó:

```bash
git init -b main
git add .
git commit -m "Release v1.0.1"
git remote add origin https://github.com/OWNER/REPO.git
git remote -v
git push -u origin main
```

## 5. Tag release

```bash
git tag -a v1.0.1 -m "Remote UAV v1.0.1"
git push origin v1.0.1
```

## 6. Build asset release

```bash
./uavctl package
ls -lh dist/
```

Kết quả gồm ZIP sạch và `.sha256`.

Nếu dùng GitHub CLI:

```bash
gh release create v1.0.1 \
  dist/remote_UAV_v1_0_1_deploy.zip \
  dist/remote_UAV_v1_0_1_deploy.zip.sha256 \
  --title "Remote UAV v1.0.1" \
  --notes-file docs/RELEASE_NOTES_V1.0.1.md \
  --verify-tag
```

## 7. Repository settings khuyến nghị

Sau push:

- Default branch: `main`.
- Bật Issues nếu muốn nhận bug reports.
- Branch protection/ruleset cho `main`:
  - require pull request;
  - require CI checks;
  - block force push;
  - optionally require 1 review.
- Actions permissions tối thiểu cần thiết; workflow CI hiện chỉ cần read source.
- Có thể bật Private vulnerability reporting / Security Advisories.

## 8. License

Project không tự chọn license thay cho owner. Trước khi public open source, chọn license phù hợp và thêm file `LICENSE`. Nếu chưa quyết định, để repository private hoặc công khai với hiểu biết rằng không có license mặc định cấp quyền sử dụng/copy/modify.

## 9. Quy trình release tiếp theo

```text
feature/fix branch
→ pull request
→ CI PASS
→ merge main
→ update CHANGELOG + version
→ ./uavctl test
→ ./uavctl package
→ tag vX.Y.Z
→ GitHub Release + ZIP + SHA256
```

## 10. Không đưa runtime data lên GitHub

Evidence có thể lớn và chứa thông tin môi trường chạy. Nếu cần chia sẻ một reference attempt, export có chủ đích thành artifact riêng thay vì commit toàn bộ `runs/`.
