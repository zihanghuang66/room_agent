#!/usr/bin/env bash
# Source checkout entry point; foreground only, existing container environment.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode=full
case "${1:-}" in
  full|resume|review|build) mode="$1"; shift ;;
  -h|--help)
    echo 'Usage: bash scripts/run_room_004.sh [full [options] | resume RUN_DIR [options] | review RUN_DIR [options] | build]'
    echo 'Default: full room_004_test_04 from /yinxin/projects/room/data/raw/room_004/test_04.tar'
    echo 'resume/review require an explicit existing return_output/artifacts run directory.'
    exit 0 ;;
esac
if [[ "$mode" == resume || "$mode" == review ]]; then
  if [[ $# -eq 0 || "$1" == -* ]]; then
    echo "$mode requires an explicit existing run directory." >&2
    exit 2
  fi
fi
if [[ -f /opt/room/image/environment.sh ]]; then
  source /opt/room/image/environment.sh
fi
export PYTHONDONTWRITEBYTECODE=1
build_python="${ROOM_BUILD_PYTHON:-${ROOM_APP_PYTHON:-python3}}"
"$build_python" "$SCRIPT_DIR/build_room_004.py"
case "$mode" in
  build) exit 0 ;;
  full) exec bash "$SCRIPT_DIR/litereality-full-v0.3.1/run_test_04.sh" "$@" ;;
  resume) exec bash "$SCRIPT_DIR/litereality-full-v0.3.1/resume_existing.sh" "$@" ;;
  review) exec bash "$SCRIPT_DIR/litereality-batch-review-v0.1.0/run.sh" "$@" ;;
esac
