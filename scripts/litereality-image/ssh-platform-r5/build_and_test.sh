#!/usr/bin/env bash
# Run in the extracted room-image-build directory on x86_64 Docker.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../.." && pwd)
OUT="$ROOT/return_output/ssh-platform-r5"
mkdir -p "$OUT/cpu-smoke"
exec > >(tee "$OUT/build-and-test.log") 2>&1
test "$(uname -m)" = x86_64
docker image inspect room-litereality-agent:0.1.3-cuda124-r4 > "$OUT/base-inspect.json"
docker build --network=none --progress=plain \
    -t room-litereality-agent:0.1.3-cuda124-r5 "$HERE"
python3 "$HERE/check_platform_ssh.py"
docker run --rm --network=none -e ROOM_SSH_AUTOSTART=0 \
    --mount "type=bind,src=$OUT/cpu-smoke,dst=/yinxin/projects/room" \
    --mount "type=bind,src=$ROOT/scripts/smoke_image.sh,dst=/room-smoke.sh,readonly" \
    room-litereality-agent:0.1.3-cuda124-r5 bash /room-smoke.sh | tee "$OUT/cpu-smoke.log"
# Export a standard Docker tar after all checks pass.
bash "$HERE/export_raw.sh" > /dev/null
