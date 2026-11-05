#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /opt/room/image/environment.sh ]]; then source /opt/room/image/environment.sh; fi
export PATH="/opt/room/tools/codex-packaged/bin:/opt/room/tools/node/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
if [[ "${1:-}" == --background ]]; then
  shift
  if tmux has-session -t room002-resume 2>/dev/null; then
    echo 'Session exists: TERM=xterm-256color tmux attach -t room002-resume'
    exit 1
  fi
  printf -v launch '%q ' bash "$HERE/run_room_002.sh" "$@"
  TERM=xterm-256color tmux new-session -d -s room002-resume "$launch; code=\$?; echo RUN_EXIT=\$code; exec bash"
  echo 'View: TERM=xterm-256color tmux attach -t room002-resume'
  exit 0
fi
exec /opt/room/envs/app/bin/python -u "$HERE/resume.py" "$@"
