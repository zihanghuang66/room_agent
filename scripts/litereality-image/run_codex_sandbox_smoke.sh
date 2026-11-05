#!/usr/bin/env bash
# Real sandbox execution probe; no login, model calls or policy relaxation.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/.." && pwd)
OUT="$ROOT/return_output"
IMAGE=${ROOM_BUILD_IMAGE:-room-litereality-agent:0.1.3-cuda124-r3}
mkdir -p "$OUT/codex-sandbox-smoke"
exec > >(tee "$OUT/codex-sandbox-smoke.log") 2>&1
docker run --rm --network=none \
    --mount "type=bind,src=$OUT/codex-sandbox-smoke,dst=/yinxin/projects/room" \
    "$IMAGE" /opt/room/envs/app/bin/python -c '
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,"/opt/room/image")
from sandbox_probe import probe
root=Path("/yinxin/projects/room/return_output/sandbox")
root.mkdir(parents=True,exist_ok=True)
run=Path(tempfile.mkdtemp(prefix="probe-",dir=root))
report=run/"report";work=run/"work"
report.mkdir();work.mkdir()
result=probe(report,work)
(root/"result.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(result,indent=2))
raise SystemExit(0 if result["status"]=="passed" else 1)
'
