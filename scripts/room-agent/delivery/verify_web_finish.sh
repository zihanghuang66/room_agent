#!/usr/bin/env bash
# Isolated acceptance: never executes or reopens a user task.
set -Eeuo pipefail
PKG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
ENV_DIR="${ROOM_ORCH_ENV:-$PROJECT/scripts/.room-agent-stage2-v0.2.2-env}"
if [[ -f /opt/room/image/environment.sh ]]; then source /opt/room/image/environment.sh; fi
if [[ ! -x "$ENV_DIR/bin/python" ]]; then echo 'Run bash start_server.sh --check-only first.' >&2; exit 1; fi
RETURN="$PROJECT/return_output"
REPORT="$RETURN/environment_reports/room_web_finish_check_$(date -u +%Y%m%dT%H%M%SZ)_$$"
mkdir -p "$REPORT"
finish() {
  code=$?
  trap - EXIT
  "$ENV_DIR/bin/python" "$PKG/bootstrap.py" report "$REPORT" --output "$RETURN" --status "exit=$code" || true
  exit "$code"
}
trap finish EXIT
export PYTHONPATH="$PKG/room-agent/src" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
"$ENV_DIR/bin/python" "$PKG/bootstrap.py" verify "$PKG" | tee "$REPORT/package.txt"
if ! command -v node >/dev/null 2>&1; then echo 'Node.js is required for the UI acceptance check.' | tee "$REPORT/error.txt"; exit 1; fi
"$ENV_DIR/bin/python" -m pytest -q "$PKG/room-agent/tests/test_finish_queue.py" "$PKG/room-agent/tests/test_dashboard_stage3.py" "$PKG/room-agent/tests/test_diagnostics_ui.py" 2>&1 | tee "$REPORT/acceptance.log"
echo 'WEB_FINISH_CHECK_PASS: isolated HTTP, queue, graph and UI tests; existing tasks untouched.' | tee "$REPORT/result.txt"
