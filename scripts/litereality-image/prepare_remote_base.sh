#!/usr/bin/env bash
# Download a pinned public CUDA image without changing/restarting the Docker daemon.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/.." && pwd)
OUT="$ROOT/return_output"
mkdir -p "$OUT"
export HTTPS_PROXY=${ROOM_BUILD_PROXY:-http://127.0.0.1:17897}
export HTTP_PROXY="$HTTPS_PROXY"
REGCTL_SHA=3737fbbdd791e04e7a831640b681f3397a376498e80e2ad9b1b2d27296f239bc
SOURCE=nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04@sha256:0a1cb6e7bd047a1067efe14efdf0276352d5ca643dfd77963dab1a4f05a003a4
if ! printf '%s  %s\n' "$REGCTL_SHA" "$HERE/regctl" | sha256sum -c -; then
    curl --fail --location --retry 3 --max-time 300 \
        https://github.com/regclient/regclient/releases/download/v0.11.7/regctl-linux-amd64 \
        -o "$HERE/regctl.part"
    printf '%s  %s\n' "$REGCTL_SHA" "$HERE/regctl.part" | sha256sum -c -
    mv "$HERE/regctl.part" "$HERE/regctl"
fi
chmod 755 "$HERE/regctl"
"$HERE/regctl" manifest get "$SOURCE" --format raw-body > "$OUT/base-manifest.json"
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["config"]["digest"])' "$OUT/base-manifest.json")
actual=$(docker image inspect room-build-base:cuda124 --format '{{.Id}}' 2>/dev/null || true)
# Docker's containerd image store reports the manifest digest as Id; older
# engines report the image config digest. Both are pinned by SOURCE.
manifest_digest=${SOURCE##*@}
if [[ "$actual" != "$expected" && "$actual" != "$manifest_digest" ]]; then
    "$HERE/regctl" image export "$SOURCE" "$OUT/cuda124-base.tar.part" \
        --platform linux/amd64 --name room-build-base:cuda124
    mv "$OUT/cuda124-base.tar.part" "$OUT/cuda124-base.tar"
    docker load -i "$OUT/cuda124-base.tar"
fi
actual=$(docker image inspect room-build-base:cuda124 --format '{{.Id}}')
[[ "$actual" == "$expected" || "$actual" == "$manifest_digest" ]]
test "$(docker image inspect room-build-base:cuda124 --format '{{.Architecture}}')" = amd64
printf 'BASE_IMAGE_VERIFIED %s\n' "$actual"
