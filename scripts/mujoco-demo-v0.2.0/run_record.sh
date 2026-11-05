#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$script_dir/../.." && pwd)
cd "$project_dir"
if [[ -z "${DISPLAY:-}" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR) export "$key=$value";; esac
  done < <(systemctl --user show-environment)
fi
export MUJOCO_GL=glfw
python_bin="$project_dir/.venv-mujoco/bin/python"
out="$project_dir/return_output/artifacts/room_003_mujoco_physics_v001"
"$python_bin" "$script_dir/build_physics.py" \
 "$project_dir/return_output/artifacts/room_003_mujoco_v002/scene.xml" \
 "$out/blender_metadata.json" "$out"
"$python_bin" "$script_dir/demo.py" "$out" --record 2>&1 | tee "$out/record.log"
