#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DATA_DIR="$HOME/UAV/remote_uav_eval_leader_data"
PULL_SIM=1
FORCE_ENV=0
while (($#)); do
  case "$1" in
    --data-dir) DATA_DIR="$2"; shift 2;;
    --skip-pull-sim) PULL_SIM=0; shift;;
    --force-env) FORCE_ENV=1; shift;;
    -h|--help) echo "Usage: $0 [--data-dir PATH] [--skip-pull-sim] [--force-env]"; exit 0;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done
for cmd in git python3 docker; do command -v "$cmd" >/dev/null 2>&1 || { echo "Missing host command: $cmd" >&2; exit 1; }; done
docker ps >/dev/null 2>&1 || { echo "Docker is not usable by current user" >&2; exit 1; }
[[ -f ./uavctl && -f ./evaluation-uav && -f ./start-uav ]] || { echo "Run this from the Remote UAV repository" >&2; exit 1; }
chmod +x uavctl evaluation-uav start-uav
mkdir -p "$DATA_DIR/runs"
if [[ -e uav.env && "$FORCE_ENV" -ne 1 ]]; then
  echo "Keeping existing uav.env"
else
  cat > uav.env <<EOT
UAV_DATA_DIR="$DATA_DIR"
UAV_SIM_BACKEND="gazebo"
UAV_PORT="8470"
UAV_SIM_HOST_LOG_MODE="docker"
EOT
  echo "Created uav.env"
fi
if (( PULL_SIM )); then ./uavctl install --dev --pull-sim; else ./uavctl install --dev; fi
./uavctl doctor
./tools/check_evaluation_ready.sh
echo "Bootstrap complete. Next: ./tools/run_evaluation_smoke.sh"
