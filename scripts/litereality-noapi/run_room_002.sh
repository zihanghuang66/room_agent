#!/usr/bin/env bash
set -euo pipefail
room_script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
room_python="${ROOM_PYTHON:-/opt/room/envs/app/bin/python}"
if [[ ! -x "$room_python" ]]; then
  echo "ERROR: app Python missing: $room_python. Use the prepared LiteReality image or set ROOM_PYTHON." >&2
  room_python="$(command -v python3)"
fi
export PYTHONDONTWRITEBYTECODE=1
exec "$room_python" -u "$room_script_dir/run.py" "$@"
