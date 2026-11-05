#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /opt/room/image/environment.sh ]]; then
  source /opt/room/image/environment.sh
fi
export PYTHONDONTWRITEBYTECODE=1
ROOM_APP_PYTHON="${ROOM_APP_PYTHON:-/opt/room/envs/app/bin/python}"
if [[ ! -x "$ROOM_APP_PYTHON" ]]; then
  echo "Missing existing application Python: $ROOM_APP_PYTHON"
  exit 1
fi
exec "$ROOM_APP_PYTHON" -u "$SCRIPT_DIR/run_batches.py" "$@"
