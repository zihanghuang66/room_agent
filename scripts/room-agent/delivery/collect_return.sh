#!/usr/bin/env bash
set -euo pipefail
PKG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
ENV_DIR="${ROOM_ORCH_ENV:-$PROJECT/scripts/.room-agent-stage2-v0.2.0-env}"
export PYTHONPATH="$PKG/room-agent/src"
STATE="${ROOM_AGENT_STATE_ROOT:-$PROJECT/return_output/agent_state_stage2_v020}"
if [[ $# -ne 1 ]]; then echo 'Usage: bash collect_return.sh RUN_ID (shown in web page)' >&2; exit 2; fi
exec "$ENV_DIR/bin/python" -m room_agent --state-root "$STATE" pack "$1"
