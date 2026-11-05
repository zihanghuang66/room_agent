#!/usr/bin/env bash
set -euo pipefail
room_script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$room_script_dir/run_room.sh" \
  --scene-name room_002 \
  --scan-archive "${ROOM_PROJECT_ROOT:-/yinxin/projects/room}/data/raw/room_002/LiteReality-LiteReality_Scanner-Studio_9952-20261031-090455.tar" "$@"
