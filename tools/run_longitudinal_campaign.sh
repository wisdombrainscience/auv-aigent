#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOURS=""
BASE_URL="http://127.0.0.1:8470"
LOG_ROOT="${HOME}/UAV/REMOTE_UAV_EVAL_LEADER_LOGS"

usage() {
  cat <<'EOF'
Usage:
  ./tools/run_longitudinal_campaign.sh --hours 10
  ./tools/run_longitudinal_campaign.sh --hours 4
  ./tools/run_longitudinal_campaign.sh --hours 8

Options:
  --hours N       Campaign duration in hours (required; positive number)
  --base-url URL  Evaluation API base URL (default: http://127.0.0.1:8470)
  --log-root DIR  External wrapper log/evidence directory
  -h, --help      Show help
EOF
}

while (($#)); do
  case "$1" in
    --hours)
      [[ $# -ge 2 ]] || { echo "Missing value for --hours" >&2; exit 2; }
      HOURS="$2"
      shift 2
      ;;
    --base-url)
      [[ $# -ge 2 ]] || { echo "Missing value for --base-url" >&2; exit 2; }
      BASE_URL="$2"
      shift 2
      ;;
    --log-root)
      [[ $# -ge 2 ]] || { echo "Missing value for --log-root" >&2; exit 2; }
      LOG_ROOT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$HOURS" ]] || { echo "ERROR: --hours is required" >&2; usage >&2; exit 2; }

python3 - "$HOURS" <<'PY'
import sys
try:
    x = float(sys.argv[1])
except Exception:
    raise SystemExit("ERROR: --hours must be numeric")
if not (x > 0):
    raise SystemExit("ERROR: --hours must be > 0")
PY

[[ -x ./evaluation-uav ]] || {
  echo "ERROR: evaluation-uav missing/not executable" >&2
  exit 1
}

if [[ -x ./tools/check_evaluation_ready.sh ]]; then
  ./tools/check_evaluation_ready.sh
fi

mkdir -p "$LOG_ROOT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SAFE_HOURS="${HOURS//./p}"
RUN_DIR="$LOG_ROOT/longitudinal_${SAFE_HOURS}h_${STAMP}"
mkdir -p "$RUN_DIR"

LOG="$RUN_DIR/longitudinal_${SAFE_HOURS}h.log"
META="$RUN_DIR/metadata.txt"
MANIFEST="$RUN_DIR/SHA256SUMS.txt"

{
  echo "campaign=longitudinal"
  echo "requested_hours=$HOURS"
  echo "base_url=$BASE_URL"
  echo "utc_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || true)"
  echo "git_branch=$(git branch --show-current 2>/dev/null || true)"
  echo "git_status_porcelain_begin"
  git status --porcelain 2>/dev/null || true
  echo "git_status_porcelain_end"
  echo "uname=$(uname -a)"
  echo "python=$(python3 --version 2>&1 || true)"
  echo "docker=$(docker --version 2>&1 || true)"
  echo "free_disk_before:"
  df -h / || true
  echo "uav_env_selected:"
  if [[ -f uav.env ]]; then
    grep -E '^(UAV_DATA_DIR|UAV_SIM_BACKEND|UAV_PORT|UAV_SIM_HOST_LOG_MODE)=' uav.env || true
  fi
} > "$META"

echo "Longitudinal campaign"
echo "  hours:    $HOURS"
echo "  base URL: $BASE_URL"
echo "  evidence: $RUN_DIR"
echo
echo "For a continuous 10 h campaign, do not stop the run at 4 h or 8 h."

set +e
set -o pipefail
UAV_GAZEBO_GUI=off \
./evaluation-uav longitudinal \
  --hours "$HOURS" \
  --base-url "$BASE_URL" \
  2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

{
  echo "utc_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "evaluation_exit_code=$RC"
  echo "free_disk_after:"
  df -h / || true

  latest_report="$(
    find reports/evaluation -maxdepth 1 -mindepth 1 -type d \
      -name 'longitudinal_*' -printf '%T@ %p\n' 2>/dev/null \
      | sort -n \
      | tail -n 1 \
      | cut -d' ' -f2-
  )"
  echo "latest_longitudinal_report=${latest_report:-NOT_DETECTED}"
} >> "$META"

(
  cd "$RUN_DIR"
  sha256sum "$(basename "$LOG")" "$(basename "$META")" > "$(basename "$MANIFEST")"
)

echo
echo "Wrapper evidence:"
echo "  $RUN_DIR"
echo
echo "Verify later with:"
echo "  (cd \"$RUN_DIR\" && sha256sum -c SHA256SUMS.txt)"
echo
echo "evaluation_exit_code=$RC"

exit "$RC"
