#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
fail=0
warn=0
ok(){ echo "OK   $*"; }
bad(){ echo "FAIL $*" >&2; fail=1; }
note(){ echo "WARN $*" >&2; warn=1; }

echo "Remote UAV Evaluation v1.6.1 readiness check"
[[ -x ./evaluation-uav ]] && ok "evaluation-uav executable" || bad "evaluation-uav missing/not executable"
[[ -x ./uavctl ]] && ok "uavctl executable" || bad "uavctl missing/not executable"
[[ -x ./start-uav ]] && ok "start-uav executable" || bad "start-uav missing/not executable"

branch="$(git branch --show-current 2>/dev/null || true)"
[[ "$branch" == "evaluation/v1.6.1-verification" || -z "$branch" ]] && ok "evaluation branch/detached snapshot" || note "current branch: ${branch:-DETACHED}"
[[ -z "$(git status --porcelain)" ]] && ok "working tree clean" || note "working tree not clean"

command -v python3 >/dev/null 2>&1 && ok "$(python3 --version 2>&1)" || bad "python3 missing"
[[ -x .venv/bin/python ]] && ok "project virtualenv exists" || bad ".venv missing; run ./uavctl install --dev"
command -v docker >/dev/null 2>&1 || bad "docker missing"
if command -v docker >/dev/null 2>&1; then docker ps >/dev/null 2>&1 && ok "Docker usable by current user" || bad "docker ps failed"; fi

if [[ -f uav.env ]]; then
  ok "uav.env exists"
  grep -q 'UAV_SIM_BACKEND=.*gazebo' uav.env || bad "Gazebo backend not configured"
  grep -q 'UAV_SIM_HOST_LOG_MODE=.*docker' uav.env || bad "bounded Docker log mode not configured"
else
  bad "uav.env missing"
fi

if sha256sum -c docs/evaluation/baseline_v1_6_1/key_files.sha256 >/tmp/ruav-key-check.txt 2>&1; then
  ok "frozen key-file SHA256 verified"
else
  cat /tmp/ruav-key-check.txt >&2 || true
  bad "frozen key-file SHA256 failed"
fi

if (cd docs/evaluation/results && sha256sum -c SHA256SUMS.txt >/tmp/ruav-result-check.txt 2>&1); then
  ok "Comparative evidence SHA256 verified"
else
  cat /tmp/ruav-result-check.txt >&2 || true
  bad "Comparative evidence SHA256 failed"
fi

free_kb="$(df -Pk "$ROOT" | awk 'NR==2{print $4}')"
free_gb=$((free_kb/1024/1024))
echo "Free disk: ${free_gb} GiB"
(( free_gb >= 10 )) && ok "disk headroom >=10 GiB" || note "less than 10 GiB free"

docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -q '^px4io/px4-sitl-gazebo:' && ok "PX4 SITL/Gazebo image present" || note "PX4 image not visible"

if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ':8470[[:space:]]'; then note "port 8470 already in use"; else ok "port 8470 free"; fi

if (( fail )); then echo "READINESS=FAIL"; exit 1; fi
(( warn )) && echo "READINESS=PASS_WITH_WARNINGS" || echo "READINESS=PASS"
