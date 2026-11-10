#!/usr/bin/env bash
set -Eeuo pipefail
PKG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
RETURN="$PROJECT/return_output"
mkdir -p "$RETURN/environment_reports" "$PROJECT/scripts"
REPORT="$RETURN/environment_reports/room_agent_stage4b_setup_$(date -u +%Y%m%dT%H%M%SZ)_$$"
mkdir -p "$REPORT"
BOOT="${ROOM_ORCH_BOOTSTRAP_PYTHON:-python3}"
finish() {
  code=$?
  trap - EXIT
  "$BOOT" "$PKG/bootstrap.py" report "$REPORT" --output "$RETURN" --status "exit=$code" || true
  exit "$code"
}
trap finish EXIT
if [[ -f /opt/room/image/environment.sh ]]; then source /opt/room/image/environment.sh; fi
export PYTHONDONTWRITEBYTECODE=1
source "$PKG/select_python.sh"
if SELECTED="$(room_select_python 2>"$REPORT/python_candidates.log")"; then
  BOOT="$SELECTED"
else
  cat "$REPORT/python_candidates.log" >&2
  exit 1
fi
cat "$REPORT/python_candidates.log"
printf 'SELECTED_PYTHON: %s\n' "$BOOT"
"$BOOT" "$PKG/bootstrap.py" verify "$PKG" | tee "$REPORT/package.txt"
ENV_DIR="${ROOM_ORCH_ENV:-$PROJECT/scripts/.room-agent-stage2-v0.2.2-env}"
if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  "$BOOT" -m venv "$ENV_DIR" 2>&1 | tee "$REPORT/venv.log"
fi
LOCK_SHA="$("$BOOT" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$PKG/room-agent/requirements.lock")"
if [[ ! -f "$ENV_DIR/.room-lock-sha" ]] || [[ "$(cat "$ENV_DIR/.room-lock-sha")" != "$LOCK_SHA" ]]; then
  "$ENV_DIR/bin/python" -m pip install --require-hashes -r "$PKG/room-agent/requirements.lock" 2>&1 | tee "$REPORT/install.log"
  printf '%s\n' "$LOCK_SHA" > "$ENV_DIR/.room-lock-sha"
fi
export PYTHONPATH="$PKG/room-agent/src"
STATE="${ROOM_AGENT_STATE_ROOT:-$RETURN/agent_state_stage2_v020}"
"$ENV_DIR/bin/python" -m room_agent --state-root "$STATE" check-storage 2>&1 | tee "$REPORT/storage.txt"
"$ENV_DIR/bin/python" -m pytest -q "$PKG/room-agent/tests" 2>&1 | tee "$REPORT/selfcheck.txt"
# A failed planner probe must not prevent viewing existing reconstruction results.
if ! "$ENV_DIR/bin/python" "$PKG/room-agent/ui/planner_cli.py" 2>&1 | tee "$REPORT/conversation_boundary.log"; then
  echo 'CONVERSATION_UNAVAILABLE: runtime check failed; existing task UI remains available. No planning will execute without a passing boundary check.' | tee "$REPORT/conversation_warning.txt"
fi
if [[ "${1:-}" == --check-only ]]; then
  echo 'LOCAL_SELF_CHECK_PASS; real Blender/Codex/GPU checks run only when you start the corresponding web task.'
  exit 0
fi
if [[ $# -gt 0 ]]; then echo 'Usage: bash start_server.sh [--check-only]' >&2; exit 2; fi
echo "STATE_ROOT: $STATE"
echo "PROJECT: $PROJECT"
echo 'Start the SSH forwarding script on your Mac, then open http://127.0.0.1:8765'
"$ENV_DIR/bin/python" -u "$PKG/room-agent/ui/server.py" --state-root "$STATE" serve --project "$PROJECT" --port "${ROOM_WEB_PORT:-8765}" 2>&1 | tee "$REPORT/server.log"
