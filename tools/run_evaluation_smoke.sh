#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CYCLES="${1:-2}"
[[ "$CYCLES" =~ ^[1-9][0-9]*$ ]] || { echo "Usage: $0 [positive cycle count]" >&2; exit 2; }
./tools/check_evaluation_ready.sh
LOG_DIR="$HOME/UAV/REMOTE_UAV_EVAL_LEADER_LOGS"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/comparative_smoke_${CYCLES}cycles_${STAMP}.log"
set -o pipefail
UAV_GAZEBO_GUI=off ./evaluation-uav comparative --cycles "$CYCLES" 2>&1 | tee "$LOG"
echo "Smoke completed; retain the log and generated reports even on failure."
