#!/usr/bin/env bash
# Explicit optional GPU test on the build host; does not load model weights.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/.." && pwd)
OUT="$ROOT/return_output"
IMAGE=${ROOM_BUILD_IMAGE:-room-litereality-agent:0.1.3-cuda124-r3}
mkdir -p "$OUT/gpu-smoke"
exec > >(tee "$OUT/gpu-smoke.log") 2>&1
options=()
if [[ ${ROOM_GPU_USE_HOST_DRIVER:-0} == 1 ]]; then
    echo 'TEST_OVERRIDE: prefer injected host driver libraries for this older Pascal GPU; image ENV unchanged.'
    options+=(--env LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/lib/x86_64-linux-gnu)
fi
docker run --rm --network=none --gpus all "${options[@]}" \
    --mount "type=bind,src=$OUT/gpu-smoke,dst=/yinxin/projects/room" \
    --mount "type=bind,src=$HERE/smoke_gpu_imports.py,dst=/gpu-smoke.py,readonly" \
    "$IMAGE" /opt/room/envs/gpu/bin/python /gpu-smoke.py
