#!/bin/bash
set -euo pipefail
source /opt/room/image/environment.sh
/opt/room/envs/app/bin/python /opt/room/image/runtime.py
exec /opt/room/envs/app/bin/python -m litereality_agent.cli "$@"
