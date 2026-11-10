#!/usr/bin/env bash
set -euo pipefail
PKG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
ENV_DIR="${ROOM_ORCH_ENV:-$PROJECT/scripts/.room-agent-stage2-v0.2.2-env}"
STATE="${ROOM_AGENT_STATE_ROOT:-$PROJECT/return_output/agent_state_stage2_v020}"
if [[ $# -ne 1 ]]; then echo 'Usage: bash collect_diagnostics.sh RUN_ID' >&2; exit 2; fi
export PYTHONPATH="$PKG/room-agent/src"
exec "$ENV_DIR/bin/python" "$PKG/room-agent/ui/diagnostics.py" --state-root "$STATE" --output-dir "$PROJECT/return_output" "$1"
