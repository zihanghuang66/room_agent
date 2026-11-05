#!/usr/bin/env bash
set -euo pipefail
room_script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec "${ROOM_PYTHON:-/usr/bin/python3}" -B "$room_script_dir/run.py" --project-root "${ROOM_PROJECT_ROOT:-/yinxin/projects/room}" --runtime-only "$@"
