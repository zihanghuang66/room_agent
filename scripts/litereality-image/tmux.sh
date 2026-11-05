#!/bin/bash
set -euo pipefail
source /opt/room/image/environment.sh
exec tmux new-session -A -s room -c /yinxin/projects/room
