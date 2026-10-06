# Security Policy

## Supported scope

Baseline hiện tại là **localhost-only, simulation-only**. Không coi project là production flight-control stack cho UAV thật.

## Reporting

Nếu repository public, ưu tiên dùng GitHub Security Advisory / private vulnerability reporting để báo lỗ hổng có thể dẫn đến command injection, unsafe container execution, local data exposure hoặc authentication/network boundary bypass.

Không đưa token, private key, credential hoặc evidence nhạy cảm vào public issue.

## Deployment note

Không expose web console trực tiếp ra Internet. Nếu cần remote access, phải bổ sung authentication, TLS, network policy và security review riêng.
