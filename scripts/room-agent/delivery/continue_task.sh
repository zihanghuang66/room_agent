#!/usr/bin/env bash
# Continue an existing task in the foreground; never creates a new scan.
set -Eeuo pipefail
PKG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
ENV_DIR="${ROOM_ORCH_ENV:-$PROJECT/scripts/.room-agent-stage2-v0.2.2-env}"
STATE="${ROOM_AGENT_STATE_ROOT:-$PROJECT/return_output/agent_state_stage2_v020}"
if [[ $# -lt 1 || $# -gt 2 ]]; then echo 'Usage: bash continue_task.sh RUN_ID [repair|finish]' >&2; exit 2; fi
if [[ ! "$1" =~ ^[a-zA-Z0-9_-]+$ ]]; then echo 'Invalid run ID' >&2; exit 2; fi
if [[ $# -eq 2 && "$2" != repair && "$2" != finish ]]; then echo 'Action must be repair or finish' >&2; exit 2; fi
if [[ ! -x "$ENV_DIR/bin/python" ]]; then echo 'Run bash start_server.sh --check-only first.' >&2; exit 1; fi
export PYTHONPATH="$PKG/room-agent/src" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
"$ENV_DIR/bin/python" "$PKG/bootstrap.py" verify "$PKG"
finish() {
  code=$?
  trap - EXIT
  bash "$PKG/collect_diagnostics.sh" "$RUN_ID" || true
  exit "$code"
}
RUN_ID="$1"
trap finish EXIT
ARGS=(--state-root "$STATE" resume "$RUN_ID")
if [[ $# -eq 2 ]]; then ARGS+=(--action "$2"); fi
"$ENV_DIR/bin/python" -u -m room_agent "${ARGS[@]}"
