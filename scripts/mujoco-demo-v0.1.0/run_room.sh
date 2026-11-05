#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$script_dir/../.." && pwd)
python_bin="$project_dir/.venv-mujoco/bin/python"
scene="${1:-$project_dir/return_output/artifacts/room_003_mujoco_v002/scene.xml}"
if [[ ! -f "$scene" ]]; then
  echo "Missing scene: $scene" >&2
  exit 1
fi
if [[ -z "${DISPLAY:-}" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR) export "$key=$value";; esac
  done < <(systemctl --user show-environment)
fi
export MUJOCO_GL=glfw
exec "$python_bin" "$script_dir/view_room.py" "$scene"
