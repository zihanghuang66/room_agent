#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/run.sh" "/yinxin/projects/room/return_output/artifacts/room_004_test_04_full_v0.3.0_3f98a38f7e61" "$@"
