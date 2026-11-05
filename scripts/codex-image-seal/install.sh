#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /opt/room/image/environment.sh
exec /opt/room/envs/app/bin/python -u "$HERE/install.py" "$@"
