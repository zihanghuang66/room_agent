#!/usr/bin/env bash
# Remote only: export a standard Docker tar for efficient layer reuse during transfer.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../.." && pwd)
OUT="$ROOT/return_output"
IMAGE=room-litereality-agent:0.1.3-cuda124-r5
ARCHIVE="$OUT/room-litereality-agent-0.1.3-cuda124-r5.docker.tar"
exec 9>"$OUT/ssh-platform-r5/export.lock"
flock -n 9
stage=preflight
finish() {
    code=$?
    printf 'stage=%s\nexit_code=%s\nimage=%s\n' "$stage" "$code" "$IMAGE" > "$OUT/ssh-platform-r5/export.status"
}
trap finish EXIT
python3 - "$OUT" "$IMAGE" <<'PY' >&2
import json,subprocess,sys
from pathlib import Path
out=Path(sys.argv[1])
image=json.loads(subprocess.check_output(['docker','image','inspect',sys.argv[2]]))[0]
reports=[json.loads(p.read_text()) for p in (out/'ssh_checks').glob('*/result.json')]
assert any(r.get('image_id')==image['Id'] and r.get('status')=='passed' and r.get('containers_cleaned') for r in reports)
assert 'CPU_SMOKE_PASSED' in (out/'ssh-platform-r5/cpu-smoke.log').read_text()
assert image['Config']['Cmd']==['/usr/local/bin/start-ssh.sh']
(out/'ssh-platform-r5/image-inspect.json').write_text(json.dumps(image,indent=2)+'\n')
print('SSH_AND_CPU_CHECKS_VERIFIED',image['Id'])
PY
stage=streaming
printf 'stage=streaming\nexit_code=pending\nimage=%s\n' "$IMAGE" > "$OUT/ssh-platform-r5/export.status"
docker image save "$IMAGE" > "$ARCHIVE.part"
stage=verify
mv "$ARCHIVE.part" "$ARCHIVE"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256")
python3 "$HERE/verify_export.py" >&2
stage=complete
printf 'EXPORT_COMPLETE %s\n' "$ARCHIVE" >&2
