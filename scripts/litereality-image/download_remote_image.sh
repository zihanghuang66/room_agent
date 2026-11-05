#!/usr/bin/env bash
# Run on the Mac after the remote build reports stage=complete.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../.." && pwd)
DEST="$ROOT/return_output/images"
HOST=huang@192.168.3.66
REMOTE=/home/huang/room-image-build/return_output
FILE=room-litereality-agent-0.1.3-cuda124-r3.docker.tar.gz
status=$(ssh -o BatchMode=yes "$HOST" "cat $REMOTE/build.status")
printf '%s\n' "$status"
printf '%s\n' "$status" | grep -qx 'stage=complete'
printf '%s\n' "$status" | grep -qx 'exit_code=0'
mkdir -p "$DEST"
# Keep transfers under .part until independent SHA256 verification succeeds.
# Use rsync's normal temporary destination so a delta basis is not overwritten.
rsync -avP --ignore-times -e 'ssh -o BatchMode=yes' "$HOST:$REMOTE/$FILE" "$DEST/$FILE.part"
rsync -avP -e 'ssh -o BatchMode=yes' "$HOST:$REMOTE/$FILE.sha256" "$DEST/"
cd "$DEST"
read -r expected_hash expected_name < "$FILE.sha256"
[[ "$expected_name" == "$FILE" && "$expected_hash" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$expected_hash" "$FILE.part" | shasum -a 256 -c -
mv "$FILE.part" "$FILE"
printf '\nIMAGE_READY %s/%s\n' "$DEST" "$FILE"
