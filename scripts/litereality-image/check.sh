#!/bin/bash
set -euo pipefail
source /opt/room/image/environment.sh
exec /opt/room/envs/app/bin/python /opt/room/image/check.py "$@"
