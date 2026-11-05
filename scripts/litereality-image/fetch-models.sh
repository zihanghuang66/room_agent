#!/bin/bash
set -euo pipefail
source /opt/room/image/environment.sh
exec /opt/room/envs/gpu/bin/python /opt/room/image/fetch_models.py "$@"
