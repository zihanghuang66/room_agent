#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/run_test_03.sh" \
  --resume-from /yinxin/projects/room/return_output/artifacts/room_003_test_03_full_v0.2.2_16c468c8585a \
  --expected-sha b8829f85f113dc0b31a759e31291380165a5492bc4487fd960b578d2b5a63691 \
  "$@"
