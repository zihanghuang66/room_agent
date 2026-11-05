#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /opt/room/image/environment.sh ]]; then
  source /opt/room/image/environment.sh
fi
export PATH="/opt/room/tools/codex-packaged/bin:/opt/room/tools/node/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
ROOM_APP_PYTHON="${ROOM_APP_PYTHON:-/opt/room/envs/app/bin/python}"
if [[ "${1:-}" == --background ]]; then
  shift
  if tmux has-session -t room002-full 2>/dev/null; then
    echo 'room002-full already exists. Inspect it: TERM=xterm-256color tmux attach -t room002-full'
    exit 1
  fi
  printf -v launch '%q ' bash "$SCRIPT_DIR/run_room_002.sh" "$@"
  TERM=xterm-256color tmux new-session -d -s room002-full "$launch; code=\$?; echo RUN_EXIT=\$code; exec bash"
  echo 'Started. View: TERM=xterm-256color tmux attach -t room002-full'
  echo 'Detach without stopping: Ctrl-b then d. Outputs: /yinxin/projects/room/return_output'
  exit 0
fi
exec "$ROOM_APP_PYTHON" -u "$SCRIPT_DIR/run.py" "$@"
