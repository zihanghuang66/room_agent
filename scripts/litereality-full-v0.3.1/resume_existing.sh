#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /opt/room/image/environment.sh ]]; then source /opt/room/image/environment.sh; fi
export PYTHONDONTWRITEBYTECODE=1
exec "${ROOM_APP_PYTHON:-/opt/room/envs/app/bin/python}" -u "$SCRIPT_DIR/resume_existing.py" "$@"
