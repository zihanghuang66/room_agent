#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /opt/room/image/environment.sh ]]; then
  source /opt/room/image/environment.sh
fi
export PYTHONDONTWRITEBYTECODE=1
ROOM_APP_PYTHON="${ROOM_APP_PYTHON:-/opt/room/envs/app/bin/python}"
if [[ ! -x "$ROOM_APP_PYTHON" ]]; then
  echo "Missing application Python: $ROOM_APP_PYTHON. Use the prepared LiteReality container image."
  exit 1
fi
if [[ "${1:-}" == --background ]]; then
  shift
  if ! command -v tmux >/dev/null; then
    echo 'tmux is unavailable. Run this script without --background in a persistent terminal.'
    exit 1
  fi
  if TERM=xterm-256color tmux has-session -t room001-test01-v021 2>/dev/null; then
    echo 'Session already exists: TERM=xterm-256color tmux attach -t room001-test01-v021'
    exit 1
  fi
  printf -v launch '%q ' bash "$SCRIPT_DIR/run_test_01.sh" "$@"
  TERM=xterm-256color tmux new-session -d -s room001-test01-v021 "$launch; code=\$?; echo RUN_EXIT=\$code; exec bash"
  echo 'Started. View: TERM=xterm-256color tmux attach -t room001-test01-v021'
  echo 'Detach without stopping: Ctrl-b then d. Return archives: /yinxin/projects/room/return_output'
  exit 0
fi
exec "$ROOM_APP_PYTHON" -u "$SCRIPT_DIR/run.py" "$@"
