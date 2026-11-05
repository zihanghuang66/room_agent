#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/run.sh" --scene room_004_test_04 --scan-archive /yinxin/projects/room/data/raw/room_004/test_04.tar "$@"
