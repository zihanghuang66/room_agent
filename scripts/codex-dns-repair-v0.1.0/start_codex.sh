#!/usr/bin/env bash
set -euo pipefail
# A fresh process avoids retaining the old daemon's resolver state; no daemon is killed.
cd /yinxin/projects/room
exec "${ROOM_CODEX:-codex}" --no-daemon
