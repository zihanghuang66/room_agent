#!/usr/bin/env bash
# Run in the platform web Shell immediately before saving the image.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
/usr/local/bin/room-codex-runtime-check
/opt/room/envs/app/bin/python - <<'PY'
import json
from pathlib import Path
r=json.loads(Path('/opt/room/build-info/codex-seal-last.json').read_text())
if r.get('status') != 'ready_for_save_preparation' or not r.get('actual_model_execution_tested'):
    raise SystemExit('STOP: install.sh with real execution verification must pass first.')
print('CODEX_READY_FOR_SNAPSHOT')
PY
# Same host-key preparation previously used successfully for this school image.
bash "$HERE/ssh_prepare_save.sh"
