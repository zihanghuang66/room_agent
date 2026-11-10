#!/usr/bin/env bash
set -Eeuo pipefail
PKG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
ENV_DIR="${ROOM_ORCH_ENV:-$PROJECT/scripts/.room-agent-stage2-v0.2.2-env}"
if [[ -f /opt/room/image/environment.sh ]]; then source /opt/room/image/environment.sh; fi
if [[ ! -x "$ENV_DIR/bin/python" ]]; then echo 'Run bash start_server.sh --check-only first.' >&2; exit 1; fi
RETURN="$PROJECT/return_output"
REPORT="$RETURN/environment_reports/room_conversation_check_$(date -u +%Y%m%dT%H%M%SZ)_$$"
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
if ! command -v node >/dev/null 2>&1; then echo 'Node.js is required for the UI check.' | tee "$REPORT/error.txt"; exit 1; fi
"$ENV_DIR/bin/python" -m pytest -q "$PKG/room-agent/tests/test_conversation_workflows.py" "$PKG/room-agent/tests/test_task_queue.py" "$PKG/room-agent/tests/test_conversation.py" "$PKG/room-agent/tests/test_conversation_http.py" "$PKG/room-agent/tests/test_conversation_ui.py" "$PKG/room-agent/tests/test_conversation_layout.py" "$PKG/room-agent/tests/test_summary_resize.py" "$PKG/room-agent/tests/test_sidebar_layout.py" "$PKG/room-agent/tests/test_minimal_layout.py" "$PKG/room-agent/tests/test_preview_crop.py" "$PKG/room-agent/tests/test_planner_cli.py" "$PKG/room-agent/tests/test_session_edits.py" "$PKG/room-agent/tests/test_session_http.py" "$PKG/room-agent/tests/test_session_ui.py" "$PKG/room-agent/tests/test_files_clarity.py" "$PKG/room-agent/tests/test_unchanged_edit.py" "$PKG/room-agent/tests/test_scene_evidence.py" "$PKG/room-agent/tests/test_edit_contract.py" "$PKG/room-agent/tests/test_edit_driver.py" "$PKG/room-agent/tests/test_edit_acceptance.py" 2>&1 | tee "$REPORT/tests.log"
"$ENV_DIR/bin/python" "$PKG/room-agent/ui/planner_cli.py" 2>&1 | tee "$REPORT/boundary.log"
echo 'STAGE4B2A_LOCAL_CHECK_PASS: no real model calls or reconstruction; validate confirmed session editing in the web UI.' | tee "$REPORT/result.txt"
