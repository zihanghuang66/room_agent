#!/usr/bin/env bash
# Mac only: export once on Ubuntu while downloading the same verified stream.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../../.." && pwd)
DEST="$ROOT/return_output/images"
HOST=huang@192.168.3.66
REMOTE=/home/huang/room-image-build
FILE=room-litereality-agent-0.1.3-cuda124-r4.docker.tar.gz
mkdir -p "$DEST"
test ! -e "$DEST/$FILE" || { echo 'Verified delivery file already exists; refusing to overwrite.' >&2; exit 1; }
ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=20 "$HOST" \
    "bash $REMOTE/scripts/ssh-platform-r4/export_stream.sh" > "$DEST/$FILE.part"
scp -q -o BatchMode=yes "$HOST:$REMOTE/return_output/$FILE.sha256" "$DEST/"
cd "$DEST"
read -r expected_hash expected_name < "$FILE.sha256"
[[ "$expected_name" == "$FILE" && "$expected_hash" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$expected_hash" "$FILE.part" | shasum -a 256 -c -
mv "$FILE.part" "$FILE"
printf 'IMAGE_READY %s/%s\n' "$DEST" "$FILE"
