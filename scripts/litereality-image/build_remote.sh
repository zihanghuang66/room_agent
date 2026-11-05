#!/usr/bin/env bash
# Run on the authorized x86_64 Docker host; artifacts stay under return_output.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/.." && pwd)
OUT="$ROOT/return_output"
IMAGE=${ROOM_BUILD_IMAGE:-room-litereality-agent:0.1.3-cuda124-r3}
BASE=${ROOM_BUILD_BASE:-room-build-base:cuda124}
PROXY=${ROOM_BUILD_PROXY:-http://127.0.0.1:17897}
mkdir -p "$OUT"
exec 9>"$OUT/build.lock"
flock -n 9 || { echo 'A Room build is already running'; exit 1; }
exec > >(tee -a "$OUT/build.log") 2>&1
stage=preflight
printf 'stage=preflight\nexit_code=pending\nimage=%s\n' "$IMAGE" > "$OUT/build.status"
finish() {
    code=$?
    printf 'stage=%s\nexit_code=%s\nimage=%s\n' "$stage" "$code" "$IMAGE" > "$OUT/build.status"
    if (( code != 0 )); then
        printf '\nERROR: stage=%s exit=%s; see %s/build.log\n' "$stage" "$code" "$OUT"
    fi
}
trap finish EXIT
printf '\nSTART %s image=%s\n' "$(date -u +%FT%TZ)" "$IMAGE"
test "$(uname -m)" = x86_64
docker version --format '{{.Server.Version}}'
available=$(df -PB1 "$OUT" | awk 'NR==2 {print $4}')
(( available >= 60000000000 )) || { echo 'Need at least 60 GB free before build/export'; exit 1; }
docker image inspect "$BASE" --format '{{.Id}} {{.Architecture}}' | tee "$OUT/base-image.txt"
sha256sum "$HERE/Dockerfile" | tee "$OUT/dockerfile.sha256"
for url in https://pypi.org/simple/uv/ https://github.com https://download.pytorch.org/whl/cu124/ https://nodejs.org/dist/; do
    curl --proxy "$PROXY" --fail --silent --show-error --location --head --connect-timeout 15 --max-time 60 "$url" >/dev/null
    printf 'DOWNLOAD_ROUTE_OK %s\n' "$url"
done
test -f "$OUT/gpu-wheelhouse/receipt.json"
test -f "$OUT/gpu-wheelhouse/extra-receipt.json"
test -f "$OUT/gpu-wheelhouse/remaining-receipt.json"
stage=build
# Host networking is scoped to this build so RUN can use the loopback SSH proxy.
# Docker's predefined proxy args are not ENV instructions and are not persisted.
docker build --progress=plain --network=host \
    --build-context "gpuwheels=$OUT/gpu-wheelhouse" \
    --build-arg "BASE_IMAGE=$BASE" --build-arg BUILD_JOBS=1 \
    --build-arg "HTTP_PROXY=$PROXY" --build-arg "HTTPS_PROXY=$PROXY" \
    --build-arg "http_proxy=$PROXY" --build-arg "https_proxy=$PROXY" \
    --label 'org.room.base.source=nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04@sha256:0a1cb6e7bd047a1067efe14efdf0276352d5ca643dfd77963dab1a4f05a003a4' \
    --label 'org.room.build.variant=cuda124-r3' \
    --tag "$IMAGE" --file "$HERE/Dockerfile" "$HERE"
stage=cpu-smoke
mkdir -p "$OUT/cpu-smoke"
docker run --rm --network=none \
    --mount "type=bind,src=$OUT/cpu-smoke,dst=/yinxin/projects/room" \
    --mount "type=bind,src=$HERE/smoke_image.sh,dst=/room-smoke.sh,readonly" \
    "$IMAGE" bash /room-smoke.sh
stage=ssh-smoke
ROOM_BUILD_IMAGE="$IMAGE" python3 "$HERE/check_image_ssh.py"
stage=inspect
docker image inspect "$IMAGE" > "$OUT/image-inspect.json"
python3 - "$OUT/image-inspect.json" <<'PY'
import json,sys
image=json.load(open(sys.argv[1]))[0]
assert image['Architecture']=='amd64'
env=dict(s.split('=',1) for s in image['Config'].get('Env',[]))
for key in env:
    assert key.lower() not in ('http_proxy','https_proxy','all_proxy'), key
    assert not any(term in key.upper() for term in ('API_KEY','ACCESS_TOKEN','AUTH_TOKEN')), key
print('IMAGE_CONFIG_OK',image['Id'])
PY
stage=export
archive="$OUT/room-litereality-agent-0.1.3-cuda124-r3.docker.tar.gz"
docker image save "$IMAGE" | gzip -1n > "$archive.part"
gzip -t "$archive.part"
mv "$archive.part" "$archive"
(cd "$OUT" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
stage=complete
printf 'COMPLETE %s\nEXPORT %s\n' "$(date -u +%FT%TZ)" "$archive"
