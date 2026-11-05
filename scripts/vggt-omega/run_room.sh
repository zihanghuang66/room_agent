#!/usr/bin/env bash
# Default: reconstruct. Optional first argument: check, inputs, or install.
set -euo pipefail
room_script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
room_project_root="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
room_python="${ROOM_PYTHON:-/usr/bin/python3}"
room_checkpoint="${ROOM_CHECKPOINT:-$room_project_root/model/vggt_omega_1b_512.pt}"
room_action="${1:-reconstruct}"
if [[ $# -gt 0 ]]; then shift; fi

case "$room_action" in
  reconstruct)
    echo "Starting reconstruction using this package's input manifest and: $room_checkpoint"
    exec "$room_python" -B "$room_script_dir/run.py" --project-root "$room_project_root" --checkpoint "$room_checkpoint" "$@"
    ;;
  check)
    echo "Checking inputs, installed environment, GPU and preprocessing; no model inference."
    exec "$room_python" -B "$room_script_dir/preflight.py" --project-root "$room_project_root" --checkpoint "$room_checkpoint" "$@"
    ;;
  inputs)
    exec "$room_python" -B "$room_script_dir/run.py" --project-root "$room_project_root" --prepare-only "$@"
    ;;
  install)
    echo "Creating a new isolated environment. Existing working installations do not need this step."
    exec "$room_python" -B "$room_script_dir/setup.py" --project-root "$room_project_root" "$@"
    ;;
  *)
    echo "Usage: bash run_room.sh [reconstruct|check|inputs|install] [additional Python options]" >&2
    exit 2
    ;;
esac
