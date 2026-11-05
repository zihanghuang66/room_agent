#!/usr/bin/env bash
# Inside a user-provided isolated container: no model calls or persistent config edits.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source /opt/room/image/environment.sh
exec /opt/room/envs/app/bin/python "$HERE/check_codex_modes.py"
