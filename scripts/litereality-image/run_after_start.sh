#!/bin/bash
set -euo pipefail
if [[ ! -x /opt/room/envs/app/bin/python ]]; then
    echo 'ERROR: This container was not started from the Room LiteReality image.' >&2
    exit 1
fi
exec /usr/local/bin/room-check
